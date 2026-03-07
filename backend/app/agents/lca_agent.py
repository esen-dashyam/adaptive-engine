"""
Lowest Common Ancestor (LCA) search on the knowledge prerequisite graph.

Traverses backward through BUILDS_TOWARDS edges to find the nearest ancestor
node that the student has already mastered (p_mastery >= 0.95).  This
"bridge point" is the scaffold origin for bridge-prompt generation.

Usage:
    lca = find_lca(driver, student_id="s_123", node_id="sfi:MA.3.NF.A.1")
    if lca:
        # lca = {"node_id": ..., "code": ..., "description": ...,
        #        "hops": 2, "p_mastery": 0.97}
"""

from __future__ import annotations

from typing import Any

from loguru import logger

MASTERY_THRESHOLD = 0.95
MAX_HOPS          = 6


def find_lca(
    driver,
    student_id: str,
    node_id:    str,
    db:         str = "neo4j",
) -> dict[str, Any] | None:
    """
    Find the nearest mastered ancestor of *node_id* in the prerequisite DAG.

    The query walks backward through BUILDS_TOWARDS edges (up to MAX_HOPS hops)
    and returns the ancestor closest to *node_id* whose SKILL_STATE.p_mastery
    is at or above MASTERY_THRESHOLD.

    Uses Student {student_id: $sid} — consistent with BayesianSkillTracker.

    Returns
    -------
    dict with keys: node_id, code, description, hops, p_mastery
    None if no mastered ancestor is found within MAX_HOPS hops.
    """
    try:
        with driver.session(database=db) as session:
            result = session.run(
                """
                MATCH (start:StandardsFrameworkItem {identifier: $nid})
                MATCH path = (ancestor:StandardsFrameworkItem)
                             -[:BUILDS_TOWARDS*1..6]->(start)
                MATCH (s:Student {student_id: $sid})-[sk:SKILL_STATE]->(ancestor)
                WHERE sk.p_mastery >= $threshold
                WITH ancestor, sk, length(path) AS hops
                ORDER BY hops ASC
                LIMIT 1
                RETURN ancestor.identifier   AS node_id,
                       ancestor.statementCode AS code,
                       ancestor.description   AS description,
                       hops,
                       sk.p_mastery          AS p_mastery
                """,
                nid=node_id,
                sid=student_id,
                threshold=MASTERY_THRESHOLD,
            )
            row = result.single()
            if row:
                logger.debug(
                    f"LCA found: student={student_id} failed={node_id} "
                    f"lca={row['code']} hops={row['hops']}"
                )
                return dict(row)
            logger.debug(
                f"LCA: no mastered ancestor within {MAX_HOPS} hops "
                f"for student={student_id} node={node_id}"
            )
            return None
    except Exception as exc:
        logger.warning(f"LCA search failed (student={student_id}, node={node_id}): {exc}")
        return None
