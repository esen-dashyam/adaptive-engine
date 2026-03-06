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
                    weight: coalesce(r.weight, 0.7), rel_type: type(r)
                }) +
                collect(DISTINCT {
                    source: c.identifier, target: a.identifier,
                    weight: coalesce(r2.weight, 0.7), rel_type: type(r2)
                }) AS all_edges
                UNWIND all_edges AS edge
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
                    weight: coalesce(r.weight, 0.7), rel_type: type(r)
                }) + collect(DISTINCT {
                    source: c.identifier, target: a.identifier,
                    weight: coalesce(r2.weight, 0.7), rel_type: type(r2)
                }) AS all_edges
                UNWIND all_edges AS edge
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
                       n.code AS code, n.description AS description, n.grade AS grade
                """,
                ids=tested_ids,
            )
            for r in impact_result:
                node_info[r["nid"]] = {
                    "code": r["code"] or "",
                    "description": r["description"] or "",
                    "grade": r["grade"] or "",
                    "downstream_count": r["downstream_count"] or 0,
                }
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

    return {
        "gaps": gaps,
        "knowledge_state": knowledge_state,
        "hard_blocked_nodes": hard_blocked,
    }


# ── Router ────────────────────────────────────────────────────────────────────

def route_after_gaps(state: AssessmentState) -> str:
    """LangGraph conditional edge: remediate if gaps exist, else skip."""
    return "remediate" if state.gaps else "write_report"
