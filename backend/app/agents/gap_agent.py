"""
Gap Agent — KST-based knowledge state inference + gap identification.

Flow:
  fetch_kg_edges → build_kst_state → identify_gaps → rank_gaps → END

After the assessment is scored and BKT updated, this agent:
  1. Fetches the full relevant subgraph from Neo4j (PRECEDES / BUILDS_TOWARDS edges)
  2. Runs KST propagation to fill in knowledge state for untested nodes
  3. Identifies gaps: nodes where inferred mastery < threshold
  4. Ranks gaps by downstream impact (how many nodes they block)
  5. Marks hard-blocked nodes (failed 0.9+ weight prereqs)
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.agents.kst import build_knowledge_state, identify_frontier
from backend.app.core.settings import settings


MASTERY_GAP_THRESHOLD = 0.55   # below this → gap
MAX_GAPS_TO_REPORT    = 8


def _neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — fetch_kg_edges
# ─────────────────────────────────────────────────────────────────────────────

def fetch_kg_edges(state: AssessmentState) -> dict:
    """
    Fetch the KG subgraph (PRECEDES + BUILDS_TOWARDS edges) for all tested
    nodes and their 2-hop neighbourhood.  Used by KST propagation.
    """
    tested_ids = list({r.get("node_ref", "") for r in state.results if r.get("node_ref")})
    if not tested_ids:
        return {"_kg_edges": []}

    driver = _neo4j()
    try:
        with driver.session() as session:
            result = session.run(
                """
                UNWIND $ids AS nid
                MATCH (a:StandardsFrameworkItem {identifier: nid})
                OPTIONAL MATCH (a)-[r:PRECEDES|BUILDS_TOWARDS|HAS_CHILD]->(b:StandardsFrameworkItem)
                OPTIONAL MATCH (c:StandardsFrameworkItem)-[r2:PRECEDES|BUILDS_TOWARDS]->(a)
                WITH collect(DISTINCT {
                    source: a.identifier, target: b.identifier,
                    weight: coalesce(r.conceptual_weight, r.understanding_strength, 0.7), rel_type: type(r)
                }) +
                collect(DISTINCT {
                    source: c.identifier, target: a.identifier,
                    weight: coalesce(r2.conceptual_weight, r2.understanding_strength, 0.7), rel_type: type(r2)
                }) AS all_edges
                UNWIND all_edges AS edge
                WITH edge
                WHERE edge.source IS NOT NULL AND edge.target IS NOT NULL
                RETURN DISTINCT edge.source AS source, edge.target AS target,
                                edge.weight AS weight, edge.rel_type AS rel_type
                """,
                ids=tested_ids,
            )
            edges = [r.data() for r in result]
    finally:
        driver.close()

    logger.info(f"Gap Agent: fetched {len(edges)} KG edges for KST propagation")
    # Store in a temp key; orchestrator state doesn't have _kg_edges natively
    return {"_kg_edges": edges}


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — build_kst_state
# ─────────────────────────────────────────────────────────────────────────────

def build_kst_state(state: AssessmentState, kg_edges: list[dict]) -> dict:
    """
    Run KST propagation to build the full knowledge state map.
    Updates knowledge_state and hard_blocked_nodes on the agent state.
    """
    knowledge_state, hard_blocked = build_knowledge_state(
        results=state.results,
        graph_edges=kg_edges,
        misconception_weights=state.misconception_weights,
    )
    logger.info(
        f"KST: {len(knowledge_state)} nodes mapped, "
        f"{len(hard_blocked)} hard-blocked"
    )
    return {
        "knowledge_state": knowledge_state,
        "hard_blocked_nodes": hard_blocked,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 — identify_and_rank_gaps (combined node for LangGraph)
# ─────────────────────────────────────────────────────────────────────────────

def identify_and_rank_gaps(state: AssessmentState) -> dict:
    """
    Combined node: fetch edges → run KST → identify gaps → rank by impact.
    Returns updated knowledge_state, hard_blocked_nodes, and gaps list.
    """
    logger.info("━" * 60)
    logger.info(f"  PHASE B — STEP 7/12 │ identify_and_rank_gaps  (KST propagation)")
    logger.info("━" * 60)
    # Step A: fetch KG edges
    tested_ids = list({r.get("node_ref", "") for r in state.results if r.get("node_ref")})
    if not tested_ids:
        return {"gaps": [], "knowledge_state": {}, "hard_blocked_nodes": []}

    driver = _neo4j()
    edges = []
    node_info: dict[str, dict] = {}

    try:
        with driver.session() as session:
            # Edges for KST
            edge_result = session.run(
                """
                UNWIND $ids AS nid
                MATCH (a:StandardsFrameworkItem {identifier: nid})
                OPTIONAL MATCH (a)-[r:PRECEDES|BUILDS_TOWARDS|HAS_CHILD]->(b:StandardsFrameworkItem)
                OPTIONAL MATCH (c:StandardsFrameworkItem)-[r2:PRECEDES|BUILDS_TOWARDS]->(a)
                WITH collect(DISTINCT {
                    source: a.identifier, target: b.identifier,
                    weight: coalesce(r.conceptual_weight, r.understanding_strength, 0.7), rel_type: type(r)
                }) + collect(DISTINCT {
                    source: c.identifier, target: a.identifier,
                    weight: coalesce(r2.conceptual_weight, r2.understanding_strength, 0.7), rel_type: type(r2)
                }) AS all_edges
                UNWIND all_edges AS edge
                WITH edge
                WHERE edge.source IS NOT NULL AND edge.target IS NOT NULL
                RETURN DISTINCT edge.source AS source, edge.target AS target,
                                edge.weight AS weight, edge.rel_type AS rel_type
                """,
                ids=tested_ids,
            )
            edges = [r.data() for r in edge_result]

            # Downstream impact: how many nodes does each gap block?
            impact_result = session.run(
                """
                UNWIND $ids AS nid
                MATCH (n:StandardsFrameworkItem {identifier: nid})
                OPTIONAL MATCH (n)-[:PRECEDES|BUILDS_TOWARDS*1..3]->(downstream)
                RETURN nid, count(DISTINCT downstream) AS downstream_count,
                       n.statementCode AS code, n.description AS description,
                       n.gradeLevelList AS grade_list
                """,
                ids=tested_ids,
            )
            for r in impact_result:
                grade_list = r["grade_list"] or []
                node_info[r["nid"]] = {
                    "code": r["code"] or "",
                    "description": r["description"] or "",
                    "grade": grade_list[0] if grade_list else "",
                    "downstream_count": r["downstream_count"] or 0,
                }
    except Exception as exc:
        logger.warning(f"identify_and_rank_gaps Neo4j query failed (continuing with empty edges): {exc}")
    finally:
        driver.close()

    # Run KST
    knowledge_state, hard_blocked = build_knowledge_state(
        results=state.results,
        graph_edges=edges,
        misconception_weights=state.misconception_weights,
    )

    # Identify gaps
    gaps = []
    for nid, mastery in knowledge_state.items():
        if mastery < MASTERY_GAP_THRESHOLD:
            info = node_info.get(nid, {})
            gaps.append({
                "node_identifier": nid,
                "code": info.get("code", nid),
                "description": info.get("description", ""),
                "grade": info.get("grade", ""),
                "mastery_prob": round(mastery, 3),
                "hard_blocked": nid in hard_blocked,
                "downstream_blocked": info.get("downstream_count", 0),
                "priority": "high" if nid in hard_blocked else (
                    "medium" if mastery < 0.35 else "low"
                ),
            })

    # Rank: hard blocks first, then by downstream impact, then by mastery (ascending)
    gaps.sort(key=lambda g: (
        0 if g["hard_blocked"] else 1,
        -g["downstream_blocked"],
        g["mastery_prob"],
    ))
    gaps = gaps[:MAX_GAPS_TO_REPORT]

    logger.info(
        f"Gap Agent: {len(gaps)} gaps identified "
        f"({sum(1 for g in gaps if g['hard_blocked'])} hard-blocked)"
    )

    # ── Cognitive Load Pruning ────────────────────────────────────────────────
    # For every hard-blocked node, write TEMPORARY_BLOCK relationships to all
    # downstream nodes so select_standards_irt cannot surface them next session.
    newly_blocked = _prune_downstream_nodes(state.student_id, hard_blocked)

    return {
        "gaps": gaps,
        "knowledge_state": knowledge_state,
        "hard_blocked_nodes": hard_blocked,
        "newly_blocked_nodes": newly_blocked,
    }


def _prune_downstream_nodes(student_id: str, hard_blocked_ids: list[str]) -> list[str]:
    """
    For each hard-blocked node, find its downstream nodes (up to 4 hops via
    BUILDS_TOWARDS / PRECEDES) and create a
        (:Student)-[:TEMPORARY_BLOCK {blocked_by, created_at}]->(:StandardsFrameworkItem)
    relationship so the IRT selector skips them in future sessions.

    Returns the list of newly-blocked node identifiers.
    """
    if not hard_blocked_ids:
        return []

    driver = _neo4j()
    newly_blocked: list[str] = []
    try:
        with driver.session() as neo:
            from datetime import datetime
            now_str = datetime.utcnow().isoformat()

            for blocker_nid in hard_blocked_ids:
                # Find all downstream nodes
                result = neo.run(
                    """
                    MATCH (blocker:StandardsFrameworkItem {identifier: $nid})
                    MATCH (blocker)-[:BUILDS_TOWARDS|PRECEDES*1..4]->(downstream:StandardsFrameworkItem)
                    WHERE downstream.identifier <> $nid
                    RETURN DISTINCT downstream.identifier AS downstream_id
                    LIMIT 50
                    """,
                    nid=blocker_nid,
                )
                downstream_ids = [r["downstream_id"] for r in result if r["downstream_id"]]

                if not downstream_ids:
                    continue

                # Write TEMPORARY_BLOCK relationships (MERGE so idempotent)
                neo.run(
                    """
                    MATCH (s:Student {id: $sid})
                    UNWIND $downstream_ids AS did
                    MATCH (n:StandardsFrameworkItem {identifier: did})
                    MERGE (s)-[b:TEMPORARY_BLOCK]->(n)
                    ON CREATE SET b.blocked_by  = $blocker,
                                  b.created_at  = $now
                    ON MATCH  SET b.blocked_by  = $blocker,
                                  b.updated_at  = $now
                    """,
                    sid=student_id,
                    downstream_ids=downstream_ids,
                    blocker=blocker_nid,
                    now=now_str,
                )
                newly_blocked.extend(downstream_ids)
                logger.info(
                    f"Pruning: blocked {len(downstream_ids)} downstream nodes "
                    f"from hard-block [{blocker_nid}] for student {student_id}"
                )

    except Exception as exc:
        logger.warning(f"Cognitive load pruning failed (non-fatal): {exc}")
    finally:
        driver.close()

    return list(set(newly_blocked))


# ── Router ────────────────────────────────────────────────────────────────────

def route_after_gaps(state: AssessmentState) -> str:
    """LangGraph conditional edge: remediate if gaps exist, else go straight to judge_mastery."""
    return "remediate" if state.gaps else "judge"
