"""
Maximum Information Gain — Question Selection Algorithm.

The "Picker" of the adaptive test.

Goal: pick the question where the student has exactly 50% probability of
success (θ ≈ β), maximising Fisher Information and learning signal.

Graph constraint: respects the PRECEDES relationship from Neo4j.  The
selector will NOT ask a hard concept if the student just failed its
prerequisite.

Intersection bonus: if a node satisfies multiple domains simultaneously
(e.g. Word Problems + Fractions), it gets a multiplier because answering
it correctly proves mastery of two domains at once.
"""

from __future__ import annotations

import math
from typing import Any

from backend.app.agents.rasch import fisher_information, grade_to_difficulty, p_correct


# ── Weights ───────────────────────────────────────────────────────────────────

INTERSECTION_MULTIPLIER = 1.5   # bonus for nodes that span multiple domains
PREREQUISITE_BLOCK_THRESHOLD = 0.35  # if p(correct) < this on a prereq, block its children


# ── Public selector ───────────────────────────────────────────────────────────

def select_next_node(
    theta: float,
    candidates: list[dict[str, Any]],
    already_asked: set[str],
    failed_node_ids: set[str],
    prerequisite_map: dict[str, list[str]],
) -> dict[str, Any] | None:
    """
    Select the next node to test using Maximum Information Gain.

    Args:
        theta:            current student ability logit
        candidates:       list of node dicts with keys: identifier, grade, dok_level, category, domains
        already_asked:    set of node identifiers already used in this session
        failed_node_ids:  set of node identifiers the student answered wrong
        prerequisite_map: {child_id: [parent_id, ...]} — from Neo4j PRECEDES

    Returns:
        Best candidate node dict, or None if no candidates remain.
    """
    scored: list[tuple[float, dict]] = []

    for node in candidates:
        node_id = node.get("identifier", "")
        if node_id in already_asked:
            continue

        # graph constraint: skip if a hard prerequisite was failed
        if _is_blocked(node_id, failed_node_ids, prerequisite_map, theta):
            continue

        beta = grade_to_difficulty(
            node.get("grade", "5"),
            node.get("dok_level", 2),
            node.get("category", "target"),
        )
        info = fisher_information(theta, beta)

        # intersection bonus
        domains = node.get("domains", [])
        if isinstance(domains, list) and len(domains) > 1:
            info *= INTERSECTION_MULTIPLIER

        scored.append((info, node))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def rank_nodes_by_information(
    theta: float,
    candidates: list[dict[str, Any]],
) -> list[tuple[float, dict[str, Any]]]:
    """
    Return all candidates sorted by Fisher Information (descending).
    Useful for batch selection (Phase A question set assembly).
    """
    scored = []
    for node in candidates:
        beta = grade_to_difficulty(
            node.get("grade", "5"),
            node.get("dok_level", 2),
            node.get("category", "target"),
        )
        info = fisher_information(theta, beta)
        domains = node.get("domains", [])
        if isinstance(domains, list) and len(domains) > 1:
            info *= INTERSECTION_MULTIPLIER
        scored.append((info, node))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def assign_difficulties(
    nodes: list[dict[str, Any]],
) -> dict[str, float]:
    """
    Pre-compute β (difficulty logit) for every node in the batch.
    Returns {identifier: beta}.
    """
    return {
        node["identifier"]: grade_to_difficulty(
            node.get("grade", "5"),
            node.get("dok_level", 2),
            node.get("category", "target"),
        )
        for node in nodes
        if "identifier" in node
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_blocked(
    node_id: str,
    failed_ids: set[str],
    prerequisite_map: dict[str, list[str]],
    theta: float,
) -> bool:
    """
    Return True if this node should be skipped because a direct prerequisite
    was failed AND the student's θ suggests they would also fail this node.
    """
    parents = prerequisite_map.get(node_id, [])
    for parent_id in parents:
        if parent_id in failed_ids:
            return True
    return False


def build_prerequisite_map(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    """
    Build prerequisite_map from node dicts that contain a 'prerequisite_for' list.
    {child_id: [parent_id1, parent_id2, ...]}

    Node dicts can optionally include:
      "prerequisite_for": ["child_id1", ...]   (this node is a prereq for these)
      "prerequisites":    ["parent_id1", ...]  (these are prereqs for this node)
    """
    pmap: dict[str, list[str]] = {}
    for node in nodes:
        nid = node.get("identifier", "")
        for child_id in node.get("prerequisite_for", []):
            pmap.setdefault(child_id, []).append(nid)
        for parent_id in node.get("prerequisites", []):
            pmap.setdefault(nid, []).append(parent_id)
    return pmap
