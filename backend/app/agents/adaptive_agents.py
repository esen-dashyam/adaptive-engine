"""
Adaptive Assessment Agents — four new Phase B nodes.

1. detect_confusion_signal  — entry point; intercepts "I don't get this" mid-session
2. lca_safety_net           — BFS backward to find the nearest mastered ancestor
3. check_stopping_criterion — Elastic Stopping router (SE < 0.3 or count > 25?)
4. generate_follow_up_questions — when SE is still high, pull a new batch via Phase A
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.agents.lca_agent import find_lca
from backend.app.core.settings import settings

SE_THRESHOLD   = 0.30   # below this the θ estimate is considered stable
MAX_TOTAL_Q    = 25     # hard ceiling — never ask more than 25 questions total
FOLLOWUP_BATCH = 5      # how many additional questions to request if SE is still high


def _neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — detect_confusion_signal
# ─────────────────────────────────────────────────────────────────────────────

def detect_confusion_signal(state: AssessmentState) -> dict:
    """
    Entry node for Phase B.

    If the student sent a chat message without completing their answers
    (confusion_signal=True), short-circuit immediately to lca_safety_net.
    Otherwise pass through with no state change.
    """
    logger.info("━" * 60)
    logger.info("  PHASE B — ENTRY │ detect_confusion_signal")
    logger.info("━" * 60)

    if state.confusion_signal:
        logger.info(
            f"Confusion signal detected for student {state.student_id}: "
            f"\"{state.confusion_chat[:120]}\""
        )
    else:
        logger.info("No confusion signal — proceeding to score_answers")

    return {}   # routing is done via conditional edge; no state change needed


def route_confusion(state: AssessmentState) -> str:
    """LangGraph router: 'confused' → lca_safety_net, 'normal' → score_answers."""
    return "confused" if state.confusion_signal else "normal"


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — lca_safety_net
# ─────────────────────────────────────────────────────────────────────────────

def lca_safety_net(state: AssessmentState) -> dict:
    """
    Run LCA backward search for every node the student is confused about
    OR for every root prerequisite identified by detect_misconceptions.

    Populates state.lca_safety_nets:
        { standard_code: { node_id, code, description, hops, p_mastery } }

    When triggered by confusion_signal, we look at the submitted questions
    (even if unanswered) to find where to scaffold from.
    When triggered after misconceptions, we use state.misconceptions[*].root_prerequisite_code.
    """
    logger.info("━" * 60)
    logger.info("  PHASE B │ lca_safety_net  (finding scaffold anchors)")
    logger.info("━" * 60)

    driver = _neo4j()
    safety_nets: dict[str, Any] = {}

    # Collect target node identifiers to search from
    # Priority: misconception root prerequisites → then currently answered questions
    search_targets: list[tuple[str, str]] = []  # (label, node_identifier)

    if state.misconceptions:
        for m in state.misconceptions:
            code = m.get("root_prerequisite_code") or m.get("standard_code", "")
            nid  = m.get("node_ref", "")
            if nid:
                search_targets.append((code or nid, nid))

    if state.confusion_signal and not search_targets:
        # Use the nodes from the current question set
        for q in state.questions:
            nid  = q.get("node_ref", "")
            code = q.get("standard_code", nid)
            if nid:
                search_targets.append((code, nid))

    if not search_targets:
        logger.info("LCA safety net: no target nodes found — skipping")
        driver.close()
        return {"lca_safety_nets": {}}

    found = 0
    for label, nid in search_targets[:8]:   # cap to avoid slow queries
        lca = find_lca(driver, state.student_id, nid)
        if lca:
            safety_nets[label] = lca
            found += 1
            logger.info(
                f"LCA: [{label}] → safety net at '{lca['code']}' "
                f"({lca['hops']} hops, mastery={lca['p_mastery']:.2f})"
            )
        else:
            logger.info(f"LCA: [{label}] → no mastered ancestor found within 6 hops")

    driver.close()
    logger.info(f"LCA safety net: {found}/{len(search_targets[:8])} anchors found")
    return {"lca_safety_nets": safety_nets}


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 — check_stopping_criterion (used as a router)
# ─────────────────────────────────────────────────────────────────────────────

def check_stopping_criterion(state: AssessmentState) -> dict:
    """
    Pass-through node — the actual routing logic is in route_stopping().
    Logs the current SE and count for observability.
    """
    logger.info("━" * 60)
    logger.info(
        f"  PHASE B │ check_stopping_criterion  "
        f"SE={state.se:.3f}  total_answered={state.total_answered}"
    )
    if state.se < SE_THRESHOLD:
        logger.info(f"  → SE < {SE_THRESHOLD} — ability estimate is stable, continuing")
    elif state.total_answered >= MAX_TOTAL_Q:
        logger.info(f"  → {MAX_TOTAL_Q} question ceiling reached, continuing")
    else:
        logger.info(
            f"  → SE={state.se:.3f} ≥ {SE_THRESHOLD} and only {state.total_answered} answered "
            f"— requesting {FOLLOWUP_BATCH} more questions"
        )
    return {}


def route_stopping(state: AssessmentState) -> str:
    """
    LangGraph router after update_rasch.

    Returns 'continue' when the θ estimate is precise enough (SE < threshold)
    or the question ceiling has been reached.
    Returns 'more_questions' otherwise → triggers generate_follow_up_questions.
    """
    if state.se < SE_THRESHOLD or state.total_answered >= MAX_TOTAL_Q:
        return "continue"
    return "more_questions"


# ─────────────────────────────────────────────────────────────────────────────
# Node 4 — generate_follow_up_questions
# ─────────────────────────────────────────────────────────────────────────────

def generate_follow_up_questions(state: AssessmentState) -> dict:
    """
    SE is still high — generate a new small batch of questions at the
    student's updated θ, excluding nodes already asked this session.

    Uses the same Phase A logic (select_standards_irt + generate_questions)
    so question selection adapts to the updated θ after partial scoring.
    """
    logger.info("━" * 60)
    logger.info(
        f"  PHASE B │ generate_follow_up_questions  "
        f"(θ={state.theta:+.3f}, SE={state.se:.3f} — pulling {FOLLOWUP_BATCH} more)"
    )
    logger.info("━" * 60)

    from backend.app.agents.assessment_agent import select_standards_irt, generate_questions

    # Build a temporary state scoped to the follow-up batch:
    # - same student/grade/subject
    # - num_questions = FOLLOWUP_BATCH
    # - exclude already-asked node identifiers
    already_asked = {q.get("node_ref", "") for q in state.questions if q.get("node_ref")}

    # Clone relevant fields into a selection state
    selection_state = AssessmentState(
        student_id=state.student_id,
        grade=state.grade,
        subject=state.subject,
        state_jurisdiction=state.state_jurisdiction,
        theta=state.theta,
        phase="generate_followup",
    )

    # Run Phase A nodes inline
    try:
        select_out = select_standards_irt(selection_state)
        merged_state = selection_state.model_copy(update=select_out)

        # Filter out already-asked nodes from the candidate pool
        merged_state = merged_state.model_copy(update={
            "all_nodes": [
                n for n in merged_state.all_nodes
                if n.get("identifier", "") not in already_asked
            ][:FOLLOWUP_BATCH * 3],
        })

        gen_out = generate_questions(merged_state)
        new_questions = gen_out.get("questions", [])
    except Exception as exc:
        logger.error(f"generate_follow_up_questions failed: {exc}")
        new_questions = []

    logger.info(
        f"Follow-up: generated {len(new_questions)} additional questions "
        f"(excluded {len(already_asked)} already-asked nodes)"
    )

    return {
        "additional_questions": new_questions,
        "needs_more_questions": True,
    }
