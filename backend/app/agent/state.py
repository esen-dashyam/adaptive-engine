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
    # If set, assessment is pinned to these specific standard codes (retry mode)
    pinned_standard_codes: list[str] = Field(default_factory=list)

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

    # ── Exercise Memory ───────────────────────────────────────────────────────
    # standard_code → list of prior exercise records {question_text, correct, dok_level, timestamp}
    exercise_memory: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    # ── Metacognitive Agent (LLM mastery judgments) ───────────────────────────
    # standard_code → {verdict, confidence, reasoning, next_action, override_mastery}
    mastery_verdicts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # LLM recommendation decisions {final_recommendations, session_narrative, focus_concept, ...}
    llm_decisions: dict[str, Any] = Field(default_factory=dict)

    # ── Postgres session tracking ─────────────────────────────────────────────
    pg_session_id: str | None = None   # UUID string of AssessmentSession row
    pg_student_uuid: str | None = None # UUID string of Student row

    # ── Elastic Stopping (Adaptive CAT) ───────────────────────────────────────
    # SE < 0.3 → ability estimate is precise enough; SE ≥ 0.3 → need more questions
    se: float = 9.99                            # Rasch Standard Error of θ estimate
    total_answered: int = 0                     # cumulative answers across all batches
    needs_more_questions: bool = False          # True when SE is still high
    additional_questions: list[dict[str, Any]] = Field(default_factory=list)

    # ── Confusion Signal (Live Metacognitive Interrupt) ────────────────────────
    confusion_signal: bool = False              # True when student signals "I don't get this"
    confusion_chat: str = ""                    # Raw chat message from student
    lca_safety_nets: dict[str, Any] = Field(default_factory=dict)  # std_code → LCA ancestor

    # ── Fidelity Multiplier ────────────────────────────────────────────────────
    # question_id → time in milliseconds the student took to answer
    time_per_question: dict[str, float] = Field(default_factory=dict)

    # ── Neuro-Symbolic Signal Bridge (φ) ──────────────────────────────────────
    # Dynamic Weight Auditor output — one entry per answered question
    # Each entry: {phi, reason, target_node, gap_tag}
    # φ ∈ [-1.0, 1.0]:  1.0 = Fluent  0.5 = Partial  -1.0 = Hard Block
    phi_signals: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Live session chat buffer — raw student messages accumulated this session
    # Each entry: {question_id, chat_message, timestamp}
    session_context: list[dict[str, Any]] = Field(default_factory=list)

    # Recursive Pivot state — set when φ < 0 triggers a safety-net jump
    pivot_node: str | None = None          # safety-net node identifier
    pivot_instruction: str = ""            # bridge text: "Now that you know X, let's try Y"

    # ── Cognitive Load Pruning ─────────────────────────────────────────────────
    # node identifiers newly given TEMPORARY_BLOCK this session
    newly_blocked_nodes: list[str] = Field(default_factory=list)
