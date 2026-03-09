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
    newly_blocked = _prune_downstream_nodes(state.student_id, hard_blocked)

    # ── Persist KST-inferred masteries to Neo4j ───────────────────────────────
    # KST propagates mastery signals to untested-but-related nodes in memory,
    # but those inferences are never written to SKILL_STATE, so the AI tutor's
    # flat MATCH query sees only directly tested nodes.
    # We write the inferred values now so the tutor can reference them.
    directly_tested = {r.get("node_ref", "") for r in state.results if r.get("node_ref")}
    _persist_kst_inferences(state.student_id, knowledge_state, directly_tested)

    return {
        "gaps": gaps,
        "knowledge_state": knowledge_state,
        "hard_blocked_nodes": hard_blocked,
        "newly_blocked_nodes": newly_blocked,
    }


def _persist_kst_inferences(
    student_id: str,
    knowledge_state: dict[str, float],
    directly_tested: set[str],
    max_writes: int = 80,
) -> None:
    """
    Write KST-inferred masteries to Neo4j SKILL_STATE edges so the AI Tutor's
    flat MATCH query can surface related concepts the student was never directly
    tested on.

    Rules:
    - Skip nodes that already have a directly-observed SKILL_STATE (from update_bkt).
      We never overwrite real evidence with an inference.
    - Skip nodes with no statementCode / description (orphaned nodes).
    - Mark inferred edges with `inferred=true, source='kst'` so future code can
      distinguish them from direct observations.
    - If a node already has an inferred SKILL_STATE, update its mastery.
    - Capped at `max_writes` nodes (50 lowest-mastery gaps + 30 highest strengths)
      to avoid bulk-writing thousands of propagated nodes.
    """
    # Separate inferred nodes into gaps (low mastery) and strengths (high mastery)
    inferred = [
        (nid, m) for nid, m in knowledge_state.items()
        if nid not in directly_tested
    ]
    if not inferred:
        return

    # Prioritise: lowest mastery first (most important gaps), then highest mastery
    gaps_inf     = sorted([(n, m) for n, m in inferred if m < 0.55], key=lambda x: x[1])[:50]
    strengths_inf = sorted([(n, m) for n, m in inferred if m >= 0.70], key=lambda x: -x[1])[:30]
    to_write = gaps_inf + strengths_inf

    if not to_write:
        return

    driver = _neo4j()
    try:
        with driver.session() as neo:
            from datetime import datetime
            now_str = datetime.utcnow().isoformat()

            # Batch write — only touch nodes that have a real statementCode.
            # ON MATCH only updates if the existing edge is itself inferred
            # (inferred=true), so direct observations are never overwritten.
            neo.run(
                """
                UNWIND $items AS item
                MATCH (n:StandardsFrameworkItem {identifier: item.nid})
                WHERE n.statementCode IS NOT NULL AND n.description IS NOT NULL
                MERGE (s:Student {id: $sid})
                MERGE (s)-[r:SKILL_STATE]->(n)
                ON CREATE SET
                    r.p_mastery   = item.mastery,
                    r.inferred    = true,
                    r.source      = 'kst',
                    r.attempts    = 0,
                    r.correct     = 0,
                    r.last_updated = $now
                ON MATCH SET
                    r.p_mastery   = CASE WHEN coalesce(r.inferred, false) = true
                                         THEN item.mastery ELSE r.p_mastery END,
                    r.last_updated = CASE WHEN coalesce(r.inferred, false) = true
                                         THEN $now ELSE r.last_updated END
                """,
                sid=student_id,
                items=[{"nid": nid, "mastery": round(m, 3)} for nid, m in to_write],
                now=now_str,
            )

        logger.info(
            f"KST persistence: wrote {len(to_write)} inferred SKILL_STATE edges "
            f"for student {student_id} "
            f"({len(gaps_inf)} gaps, {len(strengths_inf)} strengths)"
        )
    except Exception as exc:
        logger.warning(f"KST inference persistence failed (non-fatal): {exc}")
    finally:
        driver.close()


BLOCK_STRIKE_THRESHOLD = 3   # consecutive hard-failures before TEMPORARY_BLOCK is applied


def _prune_downstream_nodes(student_id: str, hard_blocked_ids: list[str]) -> list[str]:
    """
    For each hard-blocked node, find its downstream nodes (up to 4 hops via
    BUILDS_TOWARDS / PRECEDES) and create a
        (:Student)-[:TEMPORARY_BLOCK {blocked_by, created_at}]->(:StandardsFrameworkItem)
    relationship so the IRT selector skips them in future sessions.

    Three-strike rule: a TEMPORARY_BLOCK is only written after the student has
    failed the same high-weight prerequisite BLOCK_STRIKE_THRESHOLD (3) separate
    times.  This prevents a single hallucinated question from locking the student
    out of an entire module.  The failure_streak counter lives on the SKILL_STATE
    edge and resets to 0 when mastery crosses the unblock threshold (≥ 0.65).

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
                # ── Three-strike check ────────────────────────────────────────
                streak_row = neo.run(
                    """
                    MATCH (s:Student {id: $sid})-[sk:SKILL_STATE]->
                          (n:StandardsFrameworkItem {identifier: $nid})
                    RETURN coalesce(sk.failure_streak, 0) AS streak
                    """,
                    sid=student_id, nid=blocker_nid,
                ).single()
                current_streak = int(
                    streak_row["streak"] if streak_row and streak_row["streak"] is not None else 0
                ) + 1

                # Persist the updated streak (SKILL_STATE must already exist from BKT update)
                neo.run(
                    """
                    MATCH (s:Student {id: $sid})-[sk:SKILL_STATE]->
                          (n:StandardsFrameworkItem {identifier: $nid})
                    SET sk.failure_streak = $streak
                    """,
                    sid=student_id, nid=blocker_nid, streak=current_streak,
                )

                if current_streak < BLOCK_STRIKE_THRESHOLD:
                    logger.info(
                        f"Three-strike: [{blocker_nid}] strike {current_streak}/"
                        f"{BLOCK_STRIKE_THRESHOLD} — downstream not yet blocked"
                    )
                    continue

                # ── Strike threshold reached — apply TEMPORARY_BLOCK ──────────
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

                neo.run(
                    """
                    MATCH (s:Student {id: $sid})
                    UNWIND $downstream_ids AS did
                    MATCH (n:StandardsFrameworkItem {identifier: did})
                    MERGE (s)-[b:TEMPORARY_BLOCK]->(n)
                    ON CREATE SET b.blocked_by  = $blocker,
                                  b.created_at  = $now,
                                  b.strike_count = $strikes
                    ON MATCH  SET b.blocked_by  = $blocker,
                                  b.updated_at  = $now,
                                  b.strike_count = $strikes
                    """,
                    sid=student_id,
                    downstream_ids=downstream_ids,
                    blocker=blocker_nid,
                    now=now_str,
                    strikes=current_streak,
                )
                newly_blocked.extend(downstream_ids)
                logger.info(
                    f"Pruning: blocked {len(downstream_ids)} downstream nodes "
                    f"from hard-block [{blocker_nid}] (strike {current_streak}) "
                    f"for student {student_id}"
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
