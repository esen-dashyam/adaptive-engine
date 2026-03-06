"""
Compiled LangGraph for the Adaptive Assessment Agent.

Full flow:
  select_standards → fetch_rag_context → generate_questions
    ↓  [API pauses, returns questions to UI]
    ↓  [student submits answers, API resumes]
  evaluate_answers → update_mastery → analyze_gaps
    ↓
  route_after_gaps ──► generate_remediation → write_report → END
                  └──► write_report → END

The graph is split into two sub-graphs (phases) so the API can:
  Phase A: run up to generate_questions, return questions to UI.
  Phase B: receive answers, run from evaluate_answers to write_report.

Both phases are compiled separately and invoked from the agent route.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from backend.app.agent.state import AssessmentState
from backend.app.agent.nodes import (
    analyze_gaps,
    evaluate_answers,
    fetch_rag_context,
    generate_questions,
    generate_remediation,
    route_after_gaps,
    select_standards,
    update_mastery,
    write_report,
)


def build_phase_a() -> StateGraph:
    """
    Phase A: select standards → RAG → generate questions.
    Called when the frontend starts a new assessment.
    """
    g = StateGraph(AssessmentState)

    g.add_node("select_standards", select_standards)
    g.add_node("fetch_rag_context", fetch_rag_context)
    g.add_node("generate_questions", generate_questions)

    g.set_entry_point("select_standards")
    g.add_edge("select_standards", "fetch_rag_context")
    g.add_edge("fetch_rag_context", "generate_questions")
    g.add_edge("generate_questions", END)

    return g


def build_phase_b() -> StateGraph:
    """
    Phase B: evaluate answers → BKT update → gap analysis → remediation → persist.
    Called when the frontend submits the student's answers.
    """
    g = StateGraph(AssessmentState)

    g.add_node("evaluate_answers", evaluate_answers)
    g.add_node("update_mastery", update_mastery)
    g.add_node("analyze_gaps", analyze_gaps)
    g.add_node("generate_remediation", generate_remediation)
    g.add_node("write_report", write_report)

    g.set_entry_point("evaluate_answers")
    g.add_edge("evaluate_answers", "update_mastery")
    g.add_edge("update_mastery", "analyze_gaps")
    g.add_conditional_edges(
        "analyze_gaps",
        route_after_gaps,
        {
            "remediate": "generate_remediation",
            "write_report": "write_report",
        },
    )
    g.add_edge("generate_remediation", "write_report")
    g.add_edge("write_report", END)

    return g


# ── Singletons ────────────────────────────────────────────────────────────────

_phase_a = None
_phase_b = None


def get_phase_a():
    global _phase_a
    if _phase_a is None:
        _phase_a = build_phase_a().compile()
    return _phase_a


def get_phase_b():
    global _phase_b
    if _phase_b is None:
        _phase_b = build_phase_b().compile()
    return _phase_b
