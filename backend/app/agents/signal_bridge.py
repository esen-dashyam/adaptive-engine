"""
Neuro-Symbolic Signal Bridge — φ (Fidelity Factor) system.

Translates the LLM's qualitative "hunch" about the student's understanding
into a quantitative φ ∈ [-1.0, 1.0] that the BKT formula can digest:

    P(L_{t+1}) = P(L_t|Obs) + (1 - P(L_t|Obs)) * (p_transit * φ)

φ = 1.0  Fluent      — student explained reasoning correctly
φ = 0.5  Partial     — hesitant, guessing, or "I think it's this?"
φ = 0.2  Brittle     — very fast guess on a non-trivial question
φ = 0.0  Neutral     — no chat data, wrong answer handled by BKT posterior
φ = -1.0 Hard Block  — "I don't get this" → un-learning, triggers recursive backprop

Two LangGraph nodes:
  chat_to_signal  — calls Gemini with the Dynamic Weight Auditor prompt
  recursive_pivot — when any φ < 0: find LCA safety net + generate bridge instruction
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.agents.vertex_llm import get_llm
from backend.app.core.settings import settings

# φ threshold below which recursive back-propagation is triggered
PHI_BACKPROP_THRESHOLD = -0.3


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic Weight Auditor Prompt
# ─────────────────────────────────────────────────────────────────────────────

_AUDITOR_PROMPT = """\
Role: You are the Dynamic Weight Auditor for a K-8 adaptive learning system.

Your task: For EACH question answered by the student, determine a Fidelity \
Signal φ that quantifies how genuine their understanding is. This φ directly \
modifies the BKT mastery update — it is the bridge between the student's "vibe" \
and the algorithm's math.

φ scale:
  1.0  Fluent      — student explained reasoning correctly, shows genuine understanding
  0.7  Confident   — answered correctly with moderate speed, no hesitation signals
  0.5  Partial     — hesitant ("I think...", "maybe...") OR correct but unusually fast
  0.2  Brittle     — answer in < 3 s on a non-trivial question (likely pattern match)
  0.0  No signal   — no chat, wrong answer (BKT posterior already handles the loss)
 -0.5  Struggling  — trying but has a specific hurdle, shows partial conceptual gap
 -1.0  Hard Block  — "I don't get this / why / what" → fundamental prerequisite missing

Student profile:
  Rasch θ = {theta:+.2f}  |  Grade: {grade}  |  Subject: {subject}

Questions answered this session:
{question_blocks}

For each question_id, return exactly one JSON entry:
{{
  "<question_id>": {{
    "phi": <float in [-1.0, 1.0]>,
    "reason": "<1 sentence: what signals drove this φ>",
    "gap_tag": "<specific concept sub-skill missing, e.g. 'regrouping', 'place value', or null>",
    "target_node": "<node_ref identifier if φ < 0 and a specific node is implicated, else null>"
  }}
}}

Rules:
- If chat_message is empty: infer φ purely from time_ms and correctness.
- If chat contains "I don't get / understand / why / how": φ = -1.0.
- If chat contains "I think / maybe / is it": φ = 0.5 max.
- If chat contains "Oh! / I see / Got it / makes sense": φ = 1.0 min.
- Wrong answer always caps φ at 0.0 (the BKT posterior already penalises; don't double-penalise).
- Correct answer under 3000 ms on a DOK ≥ 2 question: φ ≤ 0.5.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — chat_to_signal
# ─────────────────────────────────────────────────────────────────────────────

def chat_to_signal(state: AssessmentState) -> dict:
    """
    Dynamic Weight Auditor — LLM analyzes every per-question chat message
    to produce a Fidelity Factor φ for each answered question.

    Questions with no chat get φ inferred from time_ms + correctness alone.
    The resulting phi_signals dict is read by update_bkt to modulate the
    BKT transition term.
    """
    logger.info("━" * 60)
    logger.info("  PHASE B │ chat_to_signal  (Dynamic Weight Auditor — computing φ)")
    logger.info("━" * 60)

    if not state.results:
        return {"phi_signals": {}}

    # For open-ended assessments, score_answers already called the LLM grader
    # and pre-populated phi_signals. Reuse those and only compute heuristics
    # for any questions still missing (e.g. confusion-signal early-exit path).
    existing = dict(state.phi_signals or {})
    missing_results = [r for r in state.results if r.get("question_id") not in existing]
    if not missing_results:
        n_hard = sum(1 for v in existing.values() if v.get("phi", 0) < PHI_BACKPROP_THRESHOLD)
        logger.info(
            f"Signal Bridge: all φ pre-computed by LLM grader "
            f"({len(existing)} questions, {n_hard} hard blocks) — skipping re-analysis"
        )
        for qid, sig in existing.items():
            logger.info(f"  [{qid}] φ={sig['phi']:+.2f}  {str(sig.get('reason', ''))[:70]}")
        return {"phi_signals": existing}

    blocks = []
    for r in missing_results:
        qid      = r.get("question_id", "")
        chat     = r.get("chat_message", "") or ""
        time_ms  = r.get("time_ms", 0.0)
        correct  = r.get("is_correct", False)
        dok      = r.get("dok_level", 2)
        std      = r.get("standard_code", "")
        nref     = r.get("node_ref", "")
        question = (r.get("question") or "")[:120]

        blocks.append(
            f"question_id: {qid}\n"
            f"  standard: {std}  node_ref: {nref}  dok: {dok}\n"
            f"  question: {question}\n"
            f"  correct: {correct}  time_ms: {time_ms:.0f}\n"
            f"  chat_message: \"{chat}\"\n"
        )

    prompt = _AUDITOR_PROMPT.format(
        theta=state.theta,
        grade=state.grade,
        subject=state.subject,
        question_blocks="\n".join(blocks),
    )

    llm = get_llm()
    phi_signals: dict[str, dict] = {}

    try:
        raw = llm.generate_json(prompt)
        if isinstance(raw, dict):
            for qid, entry in raw.items():
                if not isinstance(entry, dict):
                    continue
                phi = float(entry.get("phi", 1.0))
                phi = max(-1.0, min(1.0, phi))   # clamp
                phi_signals[qid] = {
                    "phi":         phi,
                    "reason":      entry.get("reason", ""),
                    "gap_tag":     entry.get("gap_tag"),
                    "target_node": entry.get("target_node"),
                }
    except Exception as exc:
        logger.warning(f"chat_to_signal LLM call failed (falling back to heuristics): {exc}")

    # Fill in any still-missing questions with heuristic φ
    for r in missing_results:
        qid = r.get("question_id", "")
        if qid and qid not in phi_signals:
            phi_signals[qid] = _heuristic_phi(r)

    # Merge with pre-computed signals from the LLM grader (existing takes priority)
    merged = {**phi_signals, **existing}

    # Accumulate chat messages into session_context for longitudinal tracking
    context_entries = [
        {
            "question_id": r.get("question_id", ""),
            "chat_message": r.get("chat_message", "") or "",
            "phi": merged.get(r.get("question_id", ""), {}).get("phi", 1.0),
            "timestamp": datetime.utcnow().isoformat(),
        }
        for r in state.results
        if r.get("chat_message")
    ]

    n_hard_blocks = sum(1 for v in merged.values() if v["phi"] < PHI_BACKPROP_THRESHOLD)
    logger.info(
        f"Signal Bridge: φ computed for {len(merged)} questions — "
        f"{n_hard_blocks} hard blocks (φ < {PHI_BACKPROP_THRESHOLD})"
    )
    for qid, sig in merged.items():
        logger.info(f"  [{qid}] φ={sig['phi']:+.2f}  {str(sig.get('reason', ''))[:70]}")

    return {
        "phi_signals":     merged,
        "session_context": state.session_context + context_entries,
    }


def _heuristic_phi(r: dict) -> dict:
    """Fallback φ when LLM is unavailable — time + correctness heuristic."""
    correct  = r.get("is_correct", False)
    time_ms  = r.get("time_ms", 0.0)
    dok      = r.get("dok_level", 2)

    if not correct:
        return {"phi": 0.0, "reason": "Wrong answer (BKT posterior handles penalty)", "gap_tag": None, "target_node": None}
    if time_ms > 0 and time_ms < 3000 and dok >= 2:
        return {"phi": 0.2, "reason": "Correct but very fast on DOK≥2 — possible pattern match", "gap_tag": None, "target_node": None}
    if r.get("is_likely_guess"):
        return {"phi": 0.5, "reason": "Speed flag set — partial credit applied", "gap_tag": None, "target_node": None}
    return {"phi": 1.0, "reason": "Correct at reasonable pace — full credit", "gap_tag": None, "target_node": None}


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — recursive_pivot  (triggered inside exercise_chat endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def compute_pivot(
    student_id: str,
    node_identifier: str,
    node_code: str,
    concept: str,
    phi: float,
    gap_tag: str | None,
) -> dict[str, Any]:
    """
    When φ < PHI_BACKPROP_THRESHOLD:
      1. Call LCA agent to find the nearest mastered ancestor (the safety net).
      2. Ask Gemini to generate a bridge instruction that connects the safety
         net concept back to the original target concept.

    Returns:
        pivot_needed: bool
        pivot_node: dict | None  — LCA result
        bridge_instruction: str  — "Now that you know X, let's try Y again"
    """
    if phi >= PHI_BACKPROP_THRESHOLD:
        return {"pivot_needed": False, "pivot_node": None, "bridge_instruction": ""}

    from backend.app.agents.lca_agent import find_lca
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    lca_result = find_lca(driver, student_id, node_identifier)
    driver.close()

    if not lca_result:
        return {
            "pivot_needed": True,
            "pivot_node": None,
            "bridge_instruction": (
                f"Let's take a step back. Before we tackle {node_code}, "
                f"let's review the foundational concepts that support it."
            ),
        }

    # Generate a bridge instruction connecting LCA anchor → original target
    anchor_code = lca_result.get("code", "")
    anchor_desc = (lca_result.get("description") or "")[:120]
    gap_context = f" (specifically the '{gap_tag}' part)" if gap_tag else ""

    prompt = f"""\
A student is struggling with {node_code} — {concept}{gap_context}.
Their nearest mastered skill is: {anchor_code} — {anchor_desc} (confidence: {lca_result.get('p_mastery', 0):.0%}).

Write a "bridge instruction" (2–3 sentences) that:
1. Acknowledges what the student already knows ({anchor_code}).
2. Explicitly connects that knowledge to {node_code} using a concrete analogy or step.
3. Ends with an encouraging prompt to try the original problem again.

Keep it conversational, age-appropriate for K-8. No jargon. Output plain text only."""

    llm = get_llm()
    bridge = ""
    try:
        bridge = llm.generate_json(prompt)
        if isinstance(bridge, dict):
            bridge = bridge.get("text", bridge.get("instruction", str(bridge)))
        bridge = str(bridge).strip().strip('"')
    except Exception as exc:
        logger.warning(f"Bridge instruction generation failed: {exc}")
        bridge = (
            f"Good news — you already understand {anchor_code}! "
            f"Let's use that knowledge as our starting point to figure out {node_code}. "
            f"Ready to try again?"
        )

    logger.info(
        f"Recursive pivot: [{node_code}] → safety net [{anchor_code}] "
        f"({lca_result.get('hops', '?')} hops). Bridge: \"{bridge[:80]}...\""
    )

    return {
        "pivot_needed":      True,
        "pivot_node":        lca_result,
        "bridge_instruction": bridge,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FailureChain persistence helper
# ─────────────────────────────────────────────────────────────────────────────

def write_failure_chain(
    student_id: str,
    failed_node_id: str,
    failed_node_code: str | None,
    lca_result: dict | None,
    signal_source: str = "phi_negative",
) -> None:
    """
    Write an immutable FailureChain audit record to PostgreSQL.
    Called whenever φ < PHI_BACKPROP_THRESHOLD during a live exercise session.
    """
    try:
        from sqlalchemy.orm import Session as OrmSession
        from backend.app.db.base import engine
        from backend.app.db.models.chat import FailureChain

        with OrmSession(engine) as db:
            chain = FailureChain(
                student_id=student_id,
                failed_node_id=failed_node_id,
                failed_node_code=failed_node_code,
                root_prereq_node_id=lca_result.get("node_id") if lca_result else None,
                root_prereq_code=lca_result.get("code") if lca_result else None,
                signal_source=signal_source,
                hops_to_lca=lca_result.get("hops") if lca_result else None,
            )
            db.add(chain)
            db.commit()
            logger.info(
                f"FailureChain written: student={student_id} "
                f"failed={failed_node_code} lca={lca_result.get('code') if lca_result else 'none'}"
            )
    except Exception as exc:
        logger.warning(f"FailureChain write failed (non-fatal): {exc}")
