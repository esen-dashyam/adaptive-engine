"""
Master LangGraph Orchestrator — ties all agents into one adaptive loop.

Phase A (question generation):
  select_standards_irt → fetch_rag_context → generate_questions → END

Phase B (evaluation + remediation + recommendations):

  detect_confusion_signal
    ├─ "confused" → lca_safety_net → write_report (early exit with scaffold)
    └─ "normal"  → score_answers → update_rasch → check_stopping_criterion
        ├─ "more_questions" → generate_follow_up_questions → write_report (partial)
        └─ "continue"       → detect_misconceptions → lca_safety_net → update_bkt
               → consolidate_memory → load_exercise_memory
               → identify_and_rank_gaps (+ bloom/prune)
               → route_after_gaps:
                   ├─ "remediate" → generate_remediation → judge_mastery
                   └─ "judge"     → judge_mastery
               → apply_fidelity_correction
               → generate_recommendations → llm_recommendation_decider
               → write_report → END

Key additions vs previous version:
  detect_confusion_signal     — intercept live "I don't get this" signal
  lca_safety_net              — BFS backward to nearest mastered ancestor
  check_stopping_criterion    — Elastic Stopping router (SE < 0.3 or count ≥ 25)
  generate_follow_up_questions — pull another batch when θ estimate is unstable
  apply_fidelity_correction   — Neuro-Symbolic fidelity multiplier on BKT gain
  _prune_downstream_nodes     — TEMPORARY_BLOCK cognitive load pruning (in gap_agent)
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph
from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.agents.assessment_agent import (
    fetch_rag_context,
    generate_questions,
    select_standards_irt,
)
from backend.app.agents.evaluation_agent import (
    detect_misconceptions,
    score_answers,
    update_bkt,
    update_rasch,
)
from backend.app.agents.gap_agent import identify_and_rank_gaps, route_after_gaps
from backend.app.agents.memory_agent import consolidate_memory, load_exercise_memory
from backend.app.agents.metacognitive_agent import (
    apply_fidelity_correction,
    judge_mastery,
    llm_recommendation_decider,
)
from backend.app.agents.remediation_agent import generate_remediation
from backend.app.agents.recommendation_agent import generate_recommendations
from backend.app.agents.adaptive_agents import (
    check_stopping_criterion,
    detect_confusion_signal,
    generate_follow_up_questions,
    lca_safety_net,
    route_confusion,
    route_stopping,
)
from backend.app.agents.signal_bridge import chat_to_signal


# ─────────────────────────────────────────────────────────────────────────────
# Report writer node (shared)
# ─────────────────────────────────────────────────────────────────────────────

def write_report(state: AssessmentState) -> dict:
    """
    Compile the final assessment report from all agent outputs.
    Returns a summary dict that gets merged into state.
    """
    logger.info("━" * 60)
    logger.info("  PHASE B COMPLETE — FINAL REPORT  (12 nodes executed)")
    logger.info("━" * 60)
    logger.info(f"  Score         : {state.score:.2%}  ({sum(1 for r in state.results if r['is_correct'])}/{len(state.results)})")
    logger.info(f"  Rasch θ       : {state.theta:+.3f}")
    logger.info(f"  Gaps found    : {len(state.gaps)}  (hard-blocked: {len(state.hard_blocked_nodes)})")
    logger.info(f"  Misconceptions: {len(state.misconceptions)}")
    logger.info(f"  Remediation   : {len(state.remediation_plan)} gap(s) with exercises")
    logger.info(f"  Recommendations: {len(getattr(state, 'recommendations', []))} next steps")
    verdicts = getattr(state, "mastery_verdicts", {})
    if verdicts:
        mastered_count = sum(1 for v in verdicts.values() if v.get("verdict") == "mastered")
        logger.info(f"  LLM verdicts  : {len(verdicts)} concepts judged, {mastered_count} mastered")
    decisions = getattr(state, "llm_decisions", {})
    if decisions.get("focus_concept"):
        logger.info(f"  Focus concept : {decisions['focus_concept']}")
    logger.info("━" * 60)
    return {}   # state is already fully populated; nothing new to add here


# ─────────────────────────────────────────────────────────────────────────────
# Phase A graph
# ─────────────────────────────────────────────────────────────────────────────

def build_phase_a() -> StateGraph:
    """
    Phase A: select standards (IRT-ranked) → RAG context → generate questions.
    """
    g = StateGraph(AssessmentState)

    g.add_node("select_standards_irt", select_standards_irt)
    g.add_node("fetch_rag_context",    fetch_rag_context)
    g.add_node("generate_questions",   generate_questions)

    g.set_entry_point("select_standards_irt")
    g.add_edge("select_standards_irt", "fetch_rag_context")
    g.add_edge("fetch_rag_context",    "generate_questions")
    g.add_edge("generate_questions",   END)

    return g


# ─────────────────────────────────────────────────────────────────────────────
# Phase B graph
# ─────────────────────────────────────────────────────────────────────────────

def build_phase_b() -> StateGraph:
    """
    Phase B: full adaptive evaluation pipeline.

    New nodes added in this version:
      detect_confusion_signal     — entry; intercepts "I don't get this" live signal
      lca_safety_net              — BFS backward to nearest mastered anchor
      check_stopping_criterion    — Elastic Stopping pass-through (routing in route_stopping)
      generate_follow_up_questions — pull extra questions when SE ≥ 0.3
      apply_fidelity_correction   — Neuro-Symbolic fidelity multiplier on BKT gain

    route_after_gaps now routes to "judge_mastery" (renamed from "write_report")
    so both paths (gaps + no-gaps) still converge at judge_mastery.
    """
    g = StateGraph(AssessmentState)

    # ── All nodes ─────────────────────────────────────────────────────────────
    g.add_node("detect_confusion_signal",      detect_confusion_signal)
    g.add_node("lca_confusion",                lca_safety_net)   # confusion path
    g.add_node("lca_misconception",            lca_safety_net)   # post-misconception path
    g.add_node("score_answers",                score_answers)
    g.add_node("chat_to_signal",               chat_to_signal)
    g.add_node("update_rasch",                 update_rasch)
    g.add_node("check_stopping_criterion",     check_stopping_criterion)
    g.add_node("generate_follow_up_questions", generate_follow_up_questions)
    g.add_node("detect_misconceptions",        detect_misconceptions)
    g.add_node("update_bkt",                   update_bkt)
    g.add_node("consolidate_memory",           consolidate_memory)
    g.add_node("load_exercise_memory",         load_exercise_memory)
    g.add_node("identify_and_rank_gaps",       identify_and_rank_gaps)
    g.add_node("generate_remediation",         generate_remediation)
    g.add_node("judge_mastery",                judge_mastery)
    g.add_node("apply_fidelity_correction",    apply_fidelity_correction)
    g.add_node("generate_recommendations",     generate_recommendations)
    g.add_node("llm_recommendation_decider",   llm_recommendation_decider)
    g.add_node("write_report",                 write_report)

    # ── Edges ─────────────────────────────────────────────────────────────────
    g.set_entry_point("detect_confusion_signal")

    # Confusion signal gate
    g.add_conditional_edges(
        "detect_confusion_signal",
        route_confusion,
        {
            "confused": "lca_confusion",
            "normal":   "score_answers",
        },
    )
    # Confusion path → early exit with scaffold anchor info
    g.add_edge("lca_confusion", "write_report")

    # Normal path: score → φ audit → rasch → elastic stopping
    g.add_edge("score_answers",   "chat_to_signal")
    g.add_edge("chat_to_signal",  "update_rasch")
    g.add_edge("update_rasch",    "check_stopping_criterion")

    # Elastic stopping gate
    g.add_conditional_edges(
        "check_stopping_criterion",
        route_stopping,
        {
            "more_questions": "generate_follow_up_questions",
            "continue":       "detect_misconceptions",
        },
    )
    # High-SE path → partial report with additional questions for frontend
    g.add_edge("generate_follow_up_questions", "write_report")

    # Full evaluation path
    g.add_edge("detect_misconceptions", "lca_misconception")
    g.add_edge("lca_misconception",     "update_bkt")
    g.add_edge("update_bkt",            "consolidate_memory")
    g.add_edge("consolidate_memory",    "load_exercise_memory")
    g.add_edge("load_exercise_memory",  "identify_and_rank_gaps")

    # Gap routing — both paths merge at judge_mastery
    g.add_conditional_edges(
        "identify_and_rank_gaps",
        route_after_gaps,
        {
            "remediate": "generate_remediation",
            "judge":     "judge_mastery",
        },
    )
    g.add_edge("generate_remediation",       "judge_mastery")
    g.add_edge("judge_mastery",              "apply_fidelity_correction")
    g.add_edge("apply_fidelity_correction",  "generate_recommendations")
    g.add_edge("generate_recommendations",   "llm_recommendation_decider")
    g.add_edge("llm_recommendation_decider", "write_report")
    g.add_edge("write_report",               END)

    return g


# ─────────────────────────────────────────────────────────────────────────────
# Compiled singletons
# ─────────────────────────────────────────────────────────────────────────────

_phase_a_graph = None
_phase_b_graph = None  # rebuilt on first request after import


def get_phase_a():
    global _phase_a_graph
    if _phase_a_graph is None:
        _phase_a_graph = build_phase_a().compile()
    return _phase_a_graph


def get_phase_b():
    global _phase_b_graph
    if _phase_b_graph is None:
        _phase_b_graph = build_phase_b().compile()
    return _phase_b_graph


def get_orchestrator():
    """Return both compiled graphs as a tuple (phase_a, phase_b)."""
    return get_phase_a(), get_phase_b()
