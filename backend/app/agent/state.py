"""
LangGraph state schema for the Adaptive Assessment Agent.

One AssessmentState instance flows through the entire graph.
Each node returns a dict with ONLY the keys it modifies — LangGraph merges
these into the running state automatically.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AssessmentState(BaseModel):
    """Full state of one adaptive assessment run."""

    # ── Identity ──────────────────────────────────────────────────────────────
    student_id: str = ""
    grade: str = ""            # e.g. "3"
    subject: str = ""          # e.g. "Mathematics"
    framework: str = "CCSS"
    state_jurisdiction: str = "Multi-State"

    # ── Phase tracking ────────────────────────────────────────────────────────
    phase: str = "start"       # start | select | rag | generate | await_answers
                               # evaluate | update_mastery | analyze | remediate | done
    error: str | None = None

    # ── Standards selection (Neo4j) ───────────────────────────────────────────
    target_standards: list[dict[str, Any]] = Field(default_factory=list)
    prerequisite_standards: list[dict[str, Any]] = Field(default_factory=list)
    all_nodes: list[dict[str, Any]] = Field(default_factory=list)

    # ── RAG context ───────────────────────────────────────────────────────────
    rag_context_map: dict[str, Any] = Field(default_factory=dict)
    rag_prompt_block: str = ""

    # ── Generated questions (Gemini) ──────────────────────────────────────────
    questions: list[dict[str, Any]] = Field(default_factory=list)

    # ── Student answers (submitted via API) ───────────────────────────────────
    # Each item: {"question_id": str, "selected_answer": "A"|"B"|"C"|"D"}
    submitted_answers: list[dict[str, Any]] = Field(default_factory=list)

    # ── Evaluation results ────────────────────────────────────────────────────
    # Each item mirrors a question + adds: is_correct, mastery_before, mastery_after
    results: list[dict[str, Any]] = Field(default_factory=list)
    score: float = 0.0           # fraction correct 0–1

    # ── Mastery updates (BKT) ─────────────────────────────────────────────────
    # standard_code → new BKT P(mastered)
    mastery_updates: dict[str, float] = Field(default_factory=dict)

    # ── Gap analysis (Neo4j Cypher) ────────────────────────────────────────────
    # Each item: {code, description, mastery_prob, blocked_downstream}
    gaps: list[dict[str, Any]] = Field(default_factory=list)

    # ── Remediation plan (Gemini) ─────────────────────────────────────────────
    # Each item: {standard_code, description, exercises: [...], explanation: str}
    remediation_plan: list[dict[str, Any]] = Field(default_factory=list)

    # ── Recommendations (from recommendation_agent) ────────────────────────────
    # Each item: {rank, standard_code, description, why_now, how_to_start, estimated_minutes, ...}
    recommendations: list[dict[str, Any]] = Field(default_factory=list)

    # ── Rasch 1PL IRT ─────────────────────────────────────────────────────────
    # θ (theta) = student ability logit. 0.0 = average; ~3.0 = advanced; ~-3.0 = struggling.
    theta: float = 0.0
    theta_history: list[float] = Field(default_factory=list)
    # node_identifier → estimated difficulty logit (β)
    question_difficulties: dict[str, float] = Field(default_factory=dict)

    # ── Knowledge Space Theory (KST) ──────────────────────────────────────────
    # node_identifier → inferred mastery probability after KST propagation
    knowledge_state: dict[str, float] = Field(default_factory=dict)
    # node_identifiers that are hard-blocked (failed 0.9+ weight prereq)
    hard_blocked_nodes: list[str] = Field(default_factory=list)

    # ── LLM Reasoning Layer ───────────────────────────────────────────────────
    # Each item: {question_id, student_answer, correct_answer, misconception, affected_nodes}
    misconceptions: list[dict[str, Any]] = Field(default_factory=list)
    # node_identifier → temporary mastery weight penalty from misconception inference
    misconception_weights: dict[str, float] = Field(default_factory=dict)

    # ── Postgres session tracking ─────────────────────────────────────────────
    pg_session_id: str | None = None   # UUID string of AssessmentSession row
    pg_student_uuid: str | None = None # UUID string of Student row
