"""
Readiness Agent — LLM decides when to formally re-assess a student.

The LLM reviews:
  - Exercise history since last formal assessment (count, success rate, recency)
  - BKT mastery trajectory (is it trending up?)
  - Which concepts were identified as gaps in the last assessment
  - Time elapsed since last assessment

It returns a readiness verdict:
  {
    "ready": bool,
    "confidence": 0.0–1.0,
    "reasoning": str,
    "trigger_now": bool,           # suggest immediate assessment
    "exercises_until_ready": int,  # if not ready, how many more
    "concepts_to_assess": [str],   # which standard codes to focus on
    "session_focus": str           # one-sentence guidance for next session
  }

This verdict is used by:
  1. The AI Tutor — to proactively tell the student "you seem ready to test on this"
  2. GET /assessment/readiness/{student_id} — frontend can poll this to offer re-assessment
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from backend.app.agents.vertex_llm import get_llm
from backend.app.core.settings import settings

MIN_EXERCISES_BEFORE_READY = 3    # need at least this many exercises since last assessment
MIN_SUCCESS_RATE_FOR_READY = 0.65 # at least 65% success on recent exercises
MIN_BKT_FOR_READY          = 0.55 # BKT mastery must be moving toward this


def _neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


def check_assessment_readiness(
    student_id: str,
    subject: str,
    grade: str,
    concept_codes: list[str] | None = None,
) -> dict[str, Any]:
    """
    LLM-driven readiness check.

    Args:
        student_id: the student to evaluate
        subject: "math" or "english"
        grade: "3", "4", etc.
        concept_codes: optional list of standard codes to focus on (e.g. last session's gaps)

    Returns readiness verdict dict.
    """
    subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"
    driver = _neo4j()

    exercise_history: list[dict] = []
    bkt_states: list[dict] = []
    last_assessment_date: str | None = None

    try:
        with driver.session() as neo:

            # ── 1. Fetch recent exercise attempts (last 50) ───────────────────
            history_result = neo.run(
                """
                MATCH (s:Student {id: $sid})-[a:ATTEMPTED]->(q:GeneratedQuestion)
                WHERE ($codes IS NULL OR q.standard_code IN $codes)
                RETURN q.standard_code   AS standard_code,
                       q.dok_level       AS dok_level,
                       q.question_type   AS question_type,
                       q.difficulty_beta AS beta,
                       a.correct         AS correct,
                       a.timestamp       AS timestamp,
                       a.session_id      AS session_id
                ORDER BY a.timestamp DESC
                LIMIT 50
                """,
                sid=student_id,
                codes=concept_codes if concept_codes else None,
            )
            exercise_history = [rec.data() for rec in history_result]

            # ── 2. Find last formal assessment session_id and date ────────────
            # Formal assessments have many questions in one session
            if exercise_history:
                session_counts: dict[str, int] = {}
                for ex in exercise_history:
                    sid_val = ex.get("session_id") or ""
                    if sid_val:
                        session_counts[sid_val] = session_counts.get(sid_val, 0) + 1

                # A formal assessment has >= 5 questions in one session
                formal_sessions = [s for s, c in session_counts.items() if c >= 5]
                if formal_sessions:
                    # The most recent formal session
                    last_formal = next(
                        (ex for ex in exercise_history if ex.get("session_id") in formal_sessions),
                        None,
                    )
                    if last_formal:
                        last_assessment_date = last_formal.get("timestamp", "")

            # ── 3. Fetch current BKT mastery states ───────────────────────────
            bkt_result = neo.run(
                """
                MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->(n:StandardsFrameworkItem)
                WHERE n.academicSubject = $subject
                  AND ($codes IS NULL OR n.statementCode IN $codes)
                RETURN n.statementCode AS code,
                       n.description   AS description,
                       r.p_mastery     AS p_mastery,
                       r.attempts      AS attempts,
                       r.correct       AS correct,
                       r.last_updated  AS last_updated
                ORDER BY r.p_mastery ASC
                LIMIT 30
                """,
                sid=student_id,
                subject=subject_name,
                codes=concept_codes if concept_codes else None,
            )
            bkt_states = [rec.data() for rec in bkt_result]

    except Exception as exc:
        logger.warning(f"Readiness check data fetch failed: {exc}")
    finally:
        driver.close()

    # ── Quick algorithmic pre-check ───────────────────────────────────────────
    # If obviously not ready (no exercises, no BKT data), skip LLM call
    if not exercise_history:
        return {
            "ready": False,
            "confidence": 0.95,
            "reasoning": "No practice exercises completed since last assessment.",
            "trigger_now": False,
            "exercises_until_ready": MIN_EXERCISES_BEFORE_READY,
            "concepts_to_assess": concept_codes or [],
            "session_focus": "Complete some practice exercises first.",
            "data_source": "algorithmic",
        }

    # Count exercises since last formal assessment
    exercises_since_last = (
        len([e for e in exercise_history
             if e.get("session_id") not in (
                 [s for s, c in _count_sessions(exercise_history).items() if c >= 5]
             )])
        if last_assessment_date else len(exercise_history)
    )

    recent = exercise_history[:10]
    recent_correct = sum(1 for e in recent if e.get("correct"))
    recent_rate = recent_correct / len(recent) if recent else 0.0

    avg_bkt = (
        sum(float(b.get("p_mastery") or 0.1) for b in bkt_states) / len(bkt_states)
        if bkt_states else 0.1
    )

    # ── Build LLM prompt ──────────────────────────────────────────────────────
    bkt_lines = []
    for b in bkt_states[:10]:
        code = b.get("code", "?")
        pm   = float(b.get("p_mastery") or 0.1)
        att  = int(b.get("attempts") or 0)
        cor  = int(b.get("correct") or 0)
        bkt_lines.append(
            f"  {code}: mastery={pm:.2f}  ({cor}/{att} correct overall)"
        )

    exercise_lines = []
    for e in recent:
        code = e.get("standard_code", "?")
        ok   = "✓" if e.get("correct") else "✗"
        dok  = e.get("dok_level", 2)
        exercise_lines.append(f"  {ok} DOK{dok}: {code}")

    last_date_str = (
        f"Last formal assessment: {last_assessment_date[:10]}" if last_assessment_date
        else "No prior formal assessment recorded"
    )

    prompt = f"""You are an expert educational diagnostician deciding whether a student is ready for a formal re-assessment.

Student: Grade {grade} {subject_name}
{last_date_str}
Exercises completed since last assessment: {exercises_since_last}
Recent success rate (last 10 exercises): {recent_rate:.0%}
Average BKT mastery across tracked concepts: {avg_bkt:.2f}

Recent exercise results:
{chr(10).join(exercise_lines) if exercise_lines else "  No recent exercises"}

Current BKT mastery by concept:
{chr(10).join(bkt_lines) if bkt_lines else "  No BKT data"}

Guidelines:
- Ready for assessment if:
  * At least {MIN_EXERCISES_BEFORE_READY} exercises completed since last assessment
  * Recent success rate >= {MIN_SUCCESS_RATE_FOR_READY:.0%}
  * Average BKT mastery >= {MIN_BKT_FOR_READY}
  * Student shows consistent improvement trend
- NOT ready if:
  * Too few exercises (still in early practice phase)
  * Success rate is low or declining (student still struggling)
  * Mastery is stagnant (not learning from practice)
- Consider: Is the student pattern-matching exercises, or genuinely mastering the concept?

Return a JSON object:
{{
  "ready": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "<2-3 sentences explaining your verdict>",
  "trigger_now": true/false,
  "exercises_until_ready": <int, 0 if ready>,
  "concepts_to_assess": ["<standard_code>", ...],
  "session_focus": "<one-sentence guidance for the student's next session>"
}}"""

    llm = get_llm()
    try:
        raw = llm.generate_json(prompt)
        if isinstance(raw, dict):
            raw["data_source"] = "llm"
            raw["exercises_since_last"] = exercises_since_last
            raw["recent_success_rate"]  = round(recent_rate, 3)
            raw["avg_bkt_mastery"]       = round(avg_bkt, 3)
            logger.info(
                f"Readiness check for {student_id}: ready={raw.get('ready')} "
                f"confidence={raw.get('confidence')} "
                f"exercises_since_last={exercises_since_last}"
            )
            return raw
    except Exception as exc:
        logger.warning(f"Readiness LLM call failed: {exc}")

    # Fallback: algorithmic verdict
    algorithmic_ready = (
        exercises_since_last >= MIN_EXERCISES_BEFORE_READY
        and recent_rate >= MIN_SUCCESS_RATE_FOR_READY
        and avg_bkt >= MIN_BKT_FOR_READY
    )
    return {
        "ready": algorithmic_ready,
        "confidence": 0.7,
        "reasoning": (
            f"Algorithmic fallback: {exercises_since_last} exercises, "
            f"{recent_rate:.0%} success rate, avg mastery {avg_bkt:.2f}."
        ),
        "trigger_now": algorithmic_ready,
        "exercises_until_ready": max(0, MIN_EXERCISES_BEFORE_READY - exercises_since_last),
        "concepts_to_assess": concept_codes or [],
        "session_focus": "Keep practicing to improve mastery." if not algorithmic_ready else "Consider taking the assessment now.",
        "data_source": "algorithmic_fallback",
        "exercises_since_last": exercises_since_last,
        "recent_success_rate":  round(recent_rate, 3),
        "avg_bkt_mastery":       round(avg_bkt, 3),
    }


def _count_sessions(history: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ex in history:
        sid = ex.get("session_id") or ""
        if sid:
            counts[sid] = counts.get(sid, 0) + 1
    return counts
