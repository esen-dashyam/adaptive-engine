"""
Remediation Agent — Targeted Exercise Generator.

Flow:
  plan_remediation → generate_exercises → order_by_prerequisite → END

For each identified gap:
  1. Determines exercise type and difficulty based on gap priority and θ
  2. Calls Vertex AI to generate 2-3 targeted practice exercises
  3. Orders exercises by prerequisite graph order (simpler first)
  4. Attaches misconception context so exercises address the root cause

The exercises are NOT generic — they are informed by:
  - The specific misconception detected by the LLM Reasoning Layer
  - The student's Rasch θ (tailored difficulty)
  - The KST-inferred knowledge state (don't re-teach already mastered prereqs)
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from backend.app.agent.state import AssessmentState
from backend.app.agents.vertex_llm import get_llm
from backend.app.core.settings import settings


MAX_GAPS_TO_REMEDIATE = 5


def _neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Node — generate_remediation
# ─────────────────────────────────────────────────────────────────────────────

def generate_remediation(state: AssessmentState) -> dict:
    """
    Generate targeted remediation exercises for all identified gaps.
    Uses Vertex AI to create exercises informed by misconceptions + θ.
    """
    gaps = state.gaps[:MAX_GAPS_TO_REMEDIATE]
    if not gaps:
        return {"remediation_plan": []}

    grade_label  = f"Grade {state.grade.replace('K', '')}"
    subject_name = "Mathematics" if state.subject.lower() == "math" else "English Language Arts"
    theta        = state.theta

    # Build misconception context map: standard_code → misconception description
    misconception_context: dict[str, str] = {}
    for m in state.misconceptions:
        code = m.get("standard_code", "")
        if code:
            misconception_context[code] = m.get("misconception", "")

    remediation_plan = []

    for gap in gaps:
        code        = gap.get("code", "")
        description = gap.get("description", "")
        mastery     = gap.get("mastery_prob", 0.3)
        is_hard     = gap.get("hard_blocked", False)
        misconception = misconception_context.get(code, "")

        # Determine exercise type based on gap severity
        if is_hard or mastery < 0.25:
            exercise_type = "foundational re-teaching with concrete examples"
            dok_target    = 1
        elif mastery < 0.45:
            exercise_type = "guided practice with scaffolding"
            dok_target    = 2
        else:
            exercise_type = "application and problem-solving"
            dok_target    = 2

        misconception_line = (
            f"\nNOTE: The student likely has this misconception: '{misconception}'. "
            "The exercises must directly address and correct this misconception."
            if misconception else ""
        )

        prompt = f"""You are a remediation specialist creating targeted practice exercises for a {grade_label} {subject_name} student.

Student ability θ = {theta:+.2f} (0 = average; negative = struggling; positive = strong)
Gap: {code} — {description}
Current mastery probability: {mastery:.0%}
Exercise focus: {exercise_type} (DOK {dok_target}){misconception_line}

Create exactly 3 practice exercises that:
1. Are age-appropriate for {grade_label}
2. Specifically address the gap in '{description}'
3. Progress from easier to harder within the 3 exercises
4. Include a brief explanation of the underlying concept for each exercise
5. Are concrete and use real-world {subject_name} scenarios

Return ONLY a valid JSON object:
{{
  "standard_code": "{code}",
  "concept_explanation": "<2-sentence plain-English explanation for the student>",
  "exercises": [
    {{
      "order": 1,
      "type": "practice|word_problem|visual|computation",
      "question": "<exercise text>",
      "hint": "<one-sentence hint>",
      "answer": "<correct answer with working>",
      "dok_level": {dok_target}
    }}
  ]
}}"""

        llm = get_llm()
        try:
            raw = llm.generate_json(prompt)
            if isinstance(raw, dict) and raw.get("exercises"):
                plan_item = {
                    "node_identifier":    gap.get("node_identifier", ""),
                    "standard_code":      code,
                    "description":        description,
                    "mastery_before":     mastery,
                    "priority":           gap.get("priority", "medium"),
                    "hard_blocked":       is_hard,
                    "misconception":      misconception,
                    "concept_explanation": raw.get("concept_explanation", ""),
                    "exercises":          raw.get("exercises", []),
                }
                remediation_plan.append(plan_item)
            else:
                logger.warning(f"Remediation: empty/invalid response for {code}")
        except Exception as exc:
            logger.error(f"Remediation generation failed for {code}: {exc}")
            remediation_plan.append({
                "node_identifier": gap.get("node_identifier", ""),
                "standard_code":   code,
                "description":     description,
                "mastery_before":  mastery,
                "priority":        gap.get("priority", "medium"),
                "hard_blocked":    is_hard,
                "misconception":   misconception,
                "concept_explanation": "",
                "exercises":       [],
                "error":           str(exc),
            })

    # Order by prerequisite: hard-blocked first, then by mastery ascending
    remediation_plan.sort(key=lambda p: (
        0 if p["hard_blocked"] else 1,
        p["mastery_before"],
    ))

    logger.info(f"Remediation Agent: generated exercises for {len(remediation_plan)} gaps")
    return {"remediation_plan": remediation_plan}
