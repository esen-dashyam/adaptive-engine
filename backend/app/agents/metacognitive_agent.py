"""
Metacognitive Agent — LLM Reasoning Layer for Mastery Judgment + Recommendation Decisions.

Two nodes added to Phase B after gap identification:

1. judge_mastery
   The LLM receives the student's FULL exercise history for each assessed concept
   (from exercise_memory) plus current session results, BKT mastery, and Rasch θ.
   It makes a holistic judgment beyond a simple BKT threshold:
     - Has the student genuinely mastered this, or are they pattern-matching?
     - Is there a trend of improvement over multiple sessions?
     - Does the misconception pattern suggest a deeper conceptual gap?
   Output: mastery_verdicts {standard_code → {verdict, confidence, reasoning, next_action}}
   Verdict can optionally override BKT mastery for downstream agents.

2. llm_recommendation_decider
   After the algorithmic recommendation engine runs, the LLM reviews the full picture:
     - Mastery verdicts from judge_mastery
     - Algorithmic KST-frontier recommendations
     - Exercise history (what has the student already practiced?)
   The LLM:
     - Filters out concepts the student has genuinely mastered
     - Flags concepts that need reinforcement before advancing
     - Reranks or adjusts the recommendation list
     - Decides the overall session focus
   Output: llm_decisions containing enriched final_recommendations + session narrative
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.agents.vertex_llm import get_llm


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — judge_mastery
# ─────────────────────────────────────────────────────────────────────────────

def judge_mastery(state: AssessmentState) -> dict:
    """
    LLM holistic mastery judgment for each assessed concept.
    Uses full exercise history from state.exercise_memory to reason
    beyond a single session's BKT posterior.
    """
    logger.info("━" * 60)
    logger.info("  PHASE B — STEP 8/9 │ judge_mastery  (LLM holistic mastery review)")
    logger.info("━" * 60)

    # Build per-standard performance summary
    standard_profiles: dict[str, dict] = {}

    for r in state.results:
        code = r.get("standard_code", "")
        if not code:
            continue
        if code not in standard_profiles:
            standard_profiles[code] = {
                "standard_code":  code,
                "description":    r.get("question", "")[:80],
                "mastery_before": r.get("mastery_before", 0.0),
                "mastery_after":  r.get("mastery_after", 0.0),
                "current_session": [],
                "history":        state.exercise_memory.get(code, []),
            }
        standard_profiles[code]["current_session"].append({
            "correct":        r.get("is_correct", False),
            "dok_level":      r.get("dok_level", 2),
            "category":       r.get("category", "target"),
            "student_answer": r.get("student_answer", ""),
            "correct_answer": r.get("correct_answer", ""),
        })

    if not standard_profiles:
        return {"mastery_verdicts": {}}

    grade_label  = f"Grade {state.grade.replace('K', '')}"
    subject_name = "Mathematics" if state.subject.lower() == "math" else "English Language Arts"

    # Build the prompt — one entry per assessed concept
    concept_blocks = []
    for code, profile in standard_profiles.items():
        history = profile["history"]
        history_summary = ""
        if history:
            past_attempts = len(history)
            past_correct  = sum(1 for h in history if h.get("correct"))
            recent = history[:3]
            recent_str = ", ".join(
                f"{'✓' if h['correct'] else '✗'} DOK{h.get('dok_level',2)}" for h in recent
            )
            history_summary = (
                f"    Prior attempts: {past_correct}/{past_attempts} correct "
                f"(recent: {recent_str})"
            )
        else:
            history_summary = "    Prior attempts: none (first time seeing this concept)"

        session_str = ", ".join(
            f"{'✓' if a['correct'] else '✗'} DOK{a['dok_level']} [{a['category']}]"
            for a in profile["current_session"]
        )

        concept_blocks.append(
            f"Standard: {code}\n"
            f"  BKT mastery before: {profile['mastery_before']:.2f} → after: {profile['mastery_after']:.2f}\n"
            f"  This session: {session_str}\n"
            f"{history_summary}"
        )

    prompt = f"""You are an expert educational diagnostician for {grade_label} {subject_name}.

Student profile:
  - Rasch ability θ = {state.theta:+.2f}
  - Overall score this session: {state.score:.0%}
  - Misconceptions detected: {len(state.misconceptions)}

Below is each assessed standard with BKT mastery, this session's answers, and prior history.
For EACH standard, give a holistic mastery verdict that goes beyond the BKT number.
Consider: trends over time, consistency of correct answers, DOK level of correct vs incorrect, misconception patterns.

{chr(10).join(concept_blocks)}

Return a JSON object mapping standard code to a verdict object:
{{
  "<standard_code>": {{
    "verdict": "mastered|developing|struggling|unknown",
    "confidence": <0.0-1.0>,
    "reasoning": "<1-2 sentences explaining your judgment>",
    "next_action": "advance|reinforce|remediate|challenge",
    "override_mastery": <null or 0.0-1.0 if BKT estimate seems wrong>
  }}
}}

Rules:
- "mastered": student reliably answers correctly across DOK levels and sessions
- "developing": improving trend, close to mastery, needs one more reinforcement pass
- "struggling": consistent errors, possibly foundational gap
- "unknown": insufficient data (first time, only 1 question)
- next_action "advance": move to next concept; "reinforce": more practice at same level;
  "remediate": back to foundations; "challenge": push to higher DOK
- Only set override_mastery if BKT posterior seems clearly wrong given the pattern"""

    llm = get_llm()
    mastery_verdicts: dict[str, dict] = {}

    try:
        raw = llm.generate_json(prompt)
        if isinstance(raw, dict):
            for code, verdict in raw.items():
                if isinstance(verdict, dict):
                    mastery_verdicts[code] = {
                        "verdict":          verdict.get("verdict", "unknown"),
                        "confidence":       float(verdict.get("confidence", 0.5)),
                        "reasoning":        verdict.get("reasoning", ""),
                        "next_action":      verdict.get("next_action", "reinforce"),
                        "override_mastery": verdict.get("override_mastery"),
                    }
    except Exception as exc:
        logger.warning(f"judge_mastery LLM call failed (non-fatal): {exc}")

    logger.info(
        f"Metacognitive: judged {len(mastery_verdicts)} concepts — "
        f"{sum(1 for v in mastery_verdicts.values() if v['verdict'] == 'mastered')} mastered, "
        f"{sum(1 for v in mastery_verdicts.values() if v['verdict'] == 'struggling')} struggling"
    )
    return {"mastery_verdicts": mastery_verdicts}


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — llm_recommendation_decider
# ─────────────────────────────────────────────────────────────────────────────

def llm_recommendation_decider(state: AssessmentState) -> dict:
    """
    LLM reasoning layer that validates and enriches algorithmic recommendations.

    The LLM:
     1. Reviews the KST-frontier recommendations
     2. Checks mastery_verdicts — filters out anything already mastered
     3. Considers exercise history — doesn't recommend concepts the student
        has already practiced extensively without progress
     4. Decides the overall session focus and narrative
     5. Can rerank, add, or remove recommendations
    """
    logger.info("━" * 60)
    logger.info("  PHASE B — STEP 9/9 │ llm_recommendation_decider  (LLM validates + enriches)")
    logger.info("━" * 60)

    recs = getattr(state, "recommendations", [])
    verdicts = getattr(state, "mastery_verdicts", {})

    if not recs:
        return {"llm_decisions": {"session_narrative": "No recommendations to evaluate."}}

    grade_label  = f"Grade {state.grade.replace('K', '')}"
    subject_name = "Mathematics" if state.subject.lower() == "math" else "English Language Arts"

    # Build recommendation summary for the prompt
    rec_lines = []
    for r in recs[:7]:
        code     = r.get("standard_code", "")
        verdict  = verdicts.get(code, {})
        history  = state.exercise_memory.get(code, [])
        hist_str = ""
        if history:
            past_correct = sum(1 for h in history if h.get("correct"))
            hist_str = f", prior={past_correct}/{len(history)} correct"

        rec_lines.append(
            f"  Rank {r.get('rank',0)}: {code} — {r.get('description','')[:70]}\n"
            f"    Success prob: {r.get('success_prob', 0.5):.0%} | "
            f"Mastery: {r.get('current_mastery', 0.3):.2f} | "
            f"LLM verdict: {verdict.get('verdict', 'unknown')} "
            f"({verdict.get('next_action', 'unknown')}){hist_str}"
        )

    # Mastered concepts the algorithm might still recommend
    mastered_codes = [
        code for code, v in verdicts.items()
        if v.get("verdict") == "mastered"
    ]

    prompt = f"""You are a personalized learning advisor for a {grade_label} {subject_name} student.

Student profile:
  - Rasch θ = {state.theta:+.2f} | Score = {state.score:.0%}
  - Gaps identified: {len(state.gaps)}
  - Concepts confirmed mastered by LLM review: {mastered_codes or 'none'}

Algorithmic recommendations (KST frontier):
{chr(10).join(rec_lines)}

Your job:
1. FILTER: Remove any recommendation where the student is already mastered (verdict='mastered').
2. REPRIORITIZE: Move "remediate" verdicts to the top. Move "challenge" verdicts toward the end.
3. REASON: For each kept recommendation, add a 1-sentence decision rationale.
4. NARRATIVE: Write a 2-sentence session summary for the student/teacher.
5. FOCUS: Pick the single most important thing for the student to do next.

Return a JSON object:
{{
  "final_recommendations": [
    {{
      "rank": <int>,
      "standard_code": "<code>",
      "keep": true,
      "action": "advance|reinforce|remediate|challenge",
      "decision_reasoning": "<why this is the right next step>",
      "priority": "high|medium|low"
    }}
  ],
  "removed_codes": ["<code>", ...],
  "removal_reasons": {{"<code>": "<why removed>"}},
  "session_narrative": "<2-sentence summary for student/teacher>",
  "focus_concept": "<single most important standard code to work on>"
}}"""

    llm = get_llm()
    llm_decisions: dict[str, Any] = {}

    try:
        raw = llm.generate_json(prompt)
        if isinstance(raw, dict):
            llm_decisions = raw

            # Merge LLM decision_reasoning back into state.recommendations
            final_recs = raw.get("final_recommendations", [])
            decision_map = {
                item["standard_code"]: item
                for item in final_recs
                if isinstance(item, dict) and item.get("standard_code")
            }
            removed = set(raw.get("removed_codes", []))

            updated_recs = []
            for rec in recs:
                code = rec.get("standard_code", "")
                if code in removed:
                    continue  # LLM decided this is already mastered
                decision = decision_map.get(code, {})
                updated_recs.append({
                    **rec,
                    "llm_action":           decision.get("action", rec.get("difficulty", "reinforce")),
                    "decision_reasoning":   decision.get("decision_reasoning", ""),
                    "llm_priority":         decision.get("priority", "medium"),
                })

            logger.info(
                f"LLM Decider: kept {len(updated_recs)}/{len(recs)} recommendations, "
                f"removed {len(removed)} (mastered/irrelevant). "
                f"Focus: {raw.get('focus_concept', 'N/A')}"
            )
            return {
                "recommendations": updated_recs,
                "llm_decisions":   llm_decisions,
            }

    except Exception as exc:
        logger.warning(f"llm_recommendation_decider failed (non-fatal): {exc}")

    return {"llm_decisions": llm_decisions}
