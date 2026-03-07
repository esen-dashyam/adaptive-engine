"""
Master LangGraph Orchestrator — ties all agents into one adaptive loop.

Phase A (question generation):
  select_standards_irt → fetch_rag_context → generate_questions → END

Phase B (evaluation + remediation + recommendations):
  score_answers → update_rasch → detect_misconceptions → update_bkt
    → consolidate_memory (persist attempts + EMA edge weight update)
    → load_exercise_memory (fetch prior history)
    → identify_and_rank_gaps → route_after_gaps
        ├─ "remediate" → generate_remediation → judge_mastery → generate_recommendations
        │                → llm_recommendation_decider → write_report
        └─ "write_report" → judge_mastery → generate_recommendations
                          → llm_recommendation_decider → write_report → END

The two phases are compiled as separate LangGraph graphs so the API can:
  Phase A: run → pause → return questions to UI
  Phase B: resume with submitted answers → run to completion
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
from backend.app.agents.metacognitive_agent import judge_mastery, llm_recommendation_decider
from backend.app.agents.remediation_agent import generate_remediation
from backend.app.agents.recommendation_agent import generate_recommendations


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
    Phase B: evaluate answers → Rasch + BKT update → memory consolidation →
             KST gap analysis → remediation → LLM mastery judgment →
             recommendations → LLM recommendation decider → write report.

    New nodes vs original:
      consolidate_memory          — persist exercise attempts + EMA edge weight update
      load_exercise_memory        — fetch prior exercise history before remediation
      judge_mastery               — LLM holistic mastery verdict per concept
      llm_recommendation_decider  — LLM filters/reranks algorithmic recommendations
    """
    g = StateGraph(AssessmentState)

    g.add_node("score_answers",               score_answers)
    g.add_node("update_rasch",                update_rasch)
    g.add_node("detect_misconceptions",       detect_misconceptions)
    g.add_node("update_bkt",                  update_bkt)
    g.add_node("consolidate_memory",          consolidate_memory)
    g.add_node("load_exercise_memory",        load_exercise_memory)
    g.add_node("identify_and_rank_gaps",      identify_and_rank_gaps)
    g.add_node("generate_remediation",        generate_remediation)
    g.add_node("judge_mastery",               judge_mastery)
    g.add_node("generate_recommendations",    generate_recommendations)
    g.add_node("llm_recommendation_decider",  llm_recommendation_decider)
    g.add_node("write_report",                write_report)

    g.set_entry_point("score_answers")
    g.add_edge("score_answers",               "update_rasch")
    g.add_edge("update_rasch",                "detect_misconceptions")
    g.add_edge("detect_misconceptions",       "update_bkt")
    g.add_edge("update_bkt",                  "consolidate_memory")
    g.add_edge("consolidate_memory",          "load_exercise_memory")
    g.add_edge("load_exercise_memory",        "identify_and_rank_gaps")
    g.add_conditional_edges(
        "identify_and_rank_gaps",
        route_after_gaps,
        {
            "remediate":    "generate_remediation",
            "write_report": "judge_mastery",
        },
    )
    g.add_edge("generate_remediation",        "judge_mastery")
    g.add_edge("judge_mastery",               "generate_recommendations")
    g.add_edge("generate_recommendations",    "llm_recommendation_decider")
    g.add_edge("llm_recommendation_decider",  "write_report")
    g.add_edge("write_report",                END)

    return g


# ─────────────────────────────────────────────────────────────────────────────
# Compiled singletons
# ─────────────────────────────────────────────────────────────────────────────

_phase_a_graph = None
_phase_b_graph = None


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
