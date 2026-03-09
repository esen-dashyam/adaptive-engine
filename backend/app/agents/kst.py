"""
Knowledge Space Theory (KST) — Post-Assessment Knowledge State Mapper.

After the 15-question assessment, KST fills in the entire knowledge map
rather than leaving it blank for untested concepts.

Rules:
  SUCCESS propagation (downward):
    If a student passes a concept node, KST assumes they have mastered
    all prerequisite (descendant) concepts with high probability.
    Weight applied: min(1.0, observed_mastery * 0.9)

  FAILURE propagation (upward):
    If a student fails a concept node, KST marks all "ancestor"
    (advanced / dependent) concepts as Low Probability or Locked.

  Hard Block:
    A failure on a relationship weighted >= 0.9 (hard prerequisite)
    sets all child nodes to HARD_BLOCKED (mastery = 0.05).

  Conceptual Intersection adjustment:
    If the LLM Reasoning Layer detected a misconception affecting a domain,
    all nodes in that domain are penalised by the misconception_weight.

Relationship weights:
  BUILDS_TOWARDS / PRECEDES with weight >= 0.9  → hard prerequisite
  BUILDS_TOWARDS / PRECEDES with weight  0.5–0.89 → soft support
  HAS_CHILD                                       → structural (weight = 1.0)
"""

from __future__ import annotations

from typing import Any

from loguru import logger


HARD_PREREQ_THRESHOLD  = 0.9
SOFT_PREREQ_THRESHOLD  = 0.5
HARD_BLOCK_MASTERY     = 0.05   # mastery assigned when hard-blocked
SUCCESS_DECAY          = 0.90   # how much mastery propagates downward per hop

# Depth-limited attenuation: penalty runs out of steam as it travels up the graph.
# Flat multiplicative decay (× 0.70) would cascade through a 144k-node graph and
# wipe out whole grade levels from a single failed question. Instead we subtract an
# absolute penalty that shrinks with hop distance and stops entirely at h=2.
#
#   Penalty(h) = BASE_FAIL_PENALTY / log2(h + 1)
#   h=1 (direct parent):      0.15 / 1.000 = 0.150
#   h=2 (grandparent):        0.15 / 1.585 ≈ 0.095  → hard stop after this
#
# This isolates damage to immediate context while still registering a diagnostic signal.
BASE_FAIL_PENALTY    = 0.15    # logit-scale mastery subtracted at h=1
MAX_PROPAGATION_HOPS = 2       # hard stop: failure never travels beyond grandparent


# ── Public API ────────────────────────────────────────────────────────────────

def build_knowledge_state(
    results: list[dict[str, Any]],
    graph_edges: list[dict[str, Any]],
    misconception_weights: dict[str, float] | None = None,
) -> tuple[dict[str, float], list[str]]:
    """
    Build the full knowledge state from assessment results + KG edges.

    Args:
        results: list of {node_identifier, is_correct, rasch_mastery}
                 rasch_mastery is P(mastery) derived from BKT/IRT after the answer.
        graph_edges: list of {source, target, rel_type, weight}
                     from Neo4j PRECEDES / BUILDS_TOWARDS / HAS_CHILD queries.
        misconception_weights: {node_identifier: penalty (0–1)} from LLM layer.

    Returns:
        (knowledge_state, hard_blocked_nodes)
        knowledge_state: {node_identifier: mastery_probability}
        hard_blocked_nodes: list of node identifiers that are hard-blocked.
    """
    if misconception_weights is None:
        misconception_weights = {}

    # 1. Seed from direct results
    state: dict[str, float] = {}
    for r in results:
        nid = r.get("node_identifier") or r.get("node_ref", "")
        if not nid:
            continue
        state[nid] = float(r.get("rasch_mastery", r.get("mastery_after", 0.3)))

    # 2. Build adjacency from edges
    children_of: dict[str, list[tuple[str, float]]] = {}   # parent → [(child, weight)]
    parents_of:  dict[str, list[tuple[str, float]]] = {}   # child  → [(parent, weight)]

    for edge in graph_edges:
        src    = edge.get("source", "")
        tgt    = edge.get("target", "")
        weight = float(edge.get("weight", 0.5))
        if not src or not tgt:
            continue
        children_of.setdefault(src, []).append((tgt, weight))
        parents_of.setdefault(tgt, []).append((src, weight))

    # 3. Success propagation — downward (to prereqs / simpler concepts)
    passed = {nid for nid, m in state.items() if m >= 0.6}
    for nid in list(passed):
        _propagate_success(nid, state, parents_of, SUCCESS_DECAY, MAX_PROPAGATION_HOPS)

    # 4. Failure propagation — upward (to advanced / dependent concepts).
    # Uses depth-limited attenuation: penalty halves every hop and stops at h=2
    # to prevent a single failure cascading through the entire 144k-node graph.
    failed = {nid for nid, m in state.items() if m < 0.4}
    hard_blocked: list[str] = []
    for nid in list(failed):
        blocked = _propagate_failure(nid, state, children_of, hop_depth=1)
        hard_blocked.extend(blocked)

    # 5. Apply misconception penalties
    for nid, penalty in misconception_weights.items():
        if nid in state:
            state[nid] = max(0.0, state[nid] - penalty)
        else:
            state[nid] = max(0.0, 0.3 - penalty)

    # 6. Clamp all values
    state = {k: round(max(0.0, min(1.0, v)), 3) for k, v in state.items()}

    return state, list(set(hard_blocked))


def identify_frontier(
    knowledge_state: dict[str, float],
    graph_edges: list[dict[str, Any]],
    mastery_threshold: float = 0.65,
) -> list[str]:
    """
    Find the ZPD frontier: nodes that are NOT yet mastered but whose
    prerequisites ARE mastered.  These are "ready to learn" concepts.

    Returns list of node identifiers sorted by readiness.
    """
    mastered   = {nid for nid, m in knowledge_state.items() if m >= mastery_threshold}
    unmastered = {nid for nid, m in knowledge_state.items() if m <  mastery_threshold}

    parents_of: dict[str, list[str]] = {}
    for edge in graph_edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        if src and tgt:
            parents_of.setdefault(tgt, []).append(src)

    frontier = []
    for nid in unmastered:
        prereqs = parents_of.get(nid, [])
        if not prereqs or all(p in mastered for p in prereqs):
            frontier.append(nid)

    return frontier


# ── Internal propagation ──────────────────────────────────────────────────────

def _propagate_success(
    node_id: str,
    state: dict[str, float],
    parents_of: dict[str, list[tuple[str, float]]],
    decay: float,
    hops: int,
) -> None:
    """
    Propagate mastery downward (toward prerequisites).
    If you can solve Grade 5 fractions, you likely mastered Grade 3 fractions.
    """
    if hops <= 0:
        return
    current_mastery = state.get(node_id, 0.5)
    for parent_id, weight in parents_of.get(node_id, []):
        inferred = current_mastery * decay * weight
        if inferred > state.get(parent_id, 0.0):
            state[parent_id] = inferred
            _propagate_success(parent_id, state, parents_of, decay * 0.9, hops - 1)


def _propagate_failure(
    node_id: str,
    state: dict[str, float],
    children_of: dict[str, list[tuple[str, float]]],
    hop_depth: int,
) -> list[str]:
    """
    Propagate failure upward (toward advanced concepts) with depth-limited attenuation.

    Penalty at hop h: BASE_FAIL_PENALTY / log2(h + 1)
      h=1 (direct parent):  0.15 logit units
      h=2 (grandparent):   ~0.09 logit units
      h>2:                  stop — no further propagation

    Hard prerequisites (weight ≥ 0.9) set the child to HARD_BLOCK_MASTERY
    regardless of hop depth, but subsequent hops from that child still obey
    the depth limit.

    Returns list of hard-blocked node IDs.
    """
    import math
    hard_blocked = []
    if hop_depth > MAX_PROPAGATION_HOPS:
        return hard_blocked

    penalty = BASE_FAIL_PENALTY / math.log2(hop_depth + 1)

    for child_id, weight in children_of.get(node_id, []):
        if weight >= HARD_PREREQ_THRESHOLD:
            state[child_id] = HARD_BLOCK_MASTERY
            hard_blocked.append(child_id)
            hard_blocked.extend(
                _propagate_failure(child_id, state, children_of, hop_depth + 1)
            )
        elif weight >= SOFT_PREREQ_THRESHOLD:
            penalised = max(0.01, state.get(child_id, 0.5) - penalty)
            if penalised < state.get(child_id, 1.0):
                state[child_id] = penalised
                _propagate_failure(child_id, state, children_of, hop_depth + 1)

    return hard_blocked
