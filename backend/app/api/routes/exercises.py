"""
Exercises API — on-demand exercise generation for the AI Tutor.

POST /exercises/generate
  - Called when the AI Tutor decides a student needs to practice a specific concept
  - Generates 3 targeted exercises using the same Gemini prompt as remediation_agent
  - Pulls student θ and exercise history from Neo4j so exercises are personalised
  - Returns the same shape as gap_exercises[] from /assessment/evaluate
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from backend.app.core.settings import settings

router = APIRouter(prefix="/exercises", tags=["Exercises"])


# ── Request / Response models ─────────────────────────────────────────────────

class ExerciseGenerateRequest(BaseModel):
    student_id: str
    node_identifier: str = Field(..., description="Neo4j StandardsFrameworkItem identifier")
    standard_code: str   = Field(..., description="e.g. 5.NBT.A.1")
    concept: str         = Field(default="", description="Human-readable concept name")
    grade: str           = Field(default="K5", description="e.g. K3, K5")
    subject: str         = Field(default="math", description="math or english")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


def _fetch_student_context(student_id: str, node_identifier: str) -> dict:
    """
    Fetch student θ and exercise history for this node from Neo4j.
    Returns {"theta": float, "p_mastery": float, "prior_exercises": list}
    """
    driver = _neo4j()
    try:
        with driver.session(database=settings.neo4j_database) as session:
            # Student θ from mean mastery across all SKILL_STATE edges
            mastery_row = session.run(
                """
                MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->()
                WHERE r.p_mastery IS NOT NULL
                RETURN collect(r.p_mastery) AS masteries
                """,
                sid=student_id,
            ).single()
            masteries = mastery_row["masteries"] if mastery_row else []
            if masteries:
                mean_p = max(0.01, min(0.99, sum(masteries) / len(masteries)))
                theta = round(max(-4.0, min(4.0, math.log(mean_p / (1.0 - mean_p)))), 3)
            else:
                theta = 0.0

            # Current mastery + persisted sub-skill gap tags for this specific node
            skill_row = session.run(
                """
                MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->(n:StandardsFrameworkItem {identifier: $nid})
                RETURN r.p_mastery AS p_mastery,
                       coalesce(r.gap_tags, []) AS gap_tags
                """,
                sid=student_id, nid=node_identifier,
            ).single()
            p_mastery = skill_row["p_mastery"] if skill_row else 0.3
            gap_tags  = list(skill_row["gap_tags"]) if skill_row else []

            # Prior exercise history for this node
            history_rows = session.run(
                """
                MATCH (s:Student {id: $sid})-[a:ATTEMPTED]->(q:GeneratedQuestion)-[:TESTS]->(n:StandardsFrameworkItem {identifier: $nid})
                RETURN q.question_text AS question_text,
                       a.correct       AS correct,
                       coalesce(q.dok_level, 2) AS dok_level
                ORDER BY a.timestamp DESC
                LIMIT 10
                """,
                sid=student_id, nid=node_identifier,
            ).data()

        return {"theta": theta, "p_mastery": p_mastery, "prior_exercises": history_rows, "gap_tags": gap_tags}
    except Exception as exc:
        logger.warning(f"_fetch_student_context failed: {exc}")
        return {"theta": 0.0, "p_mastery": 0.3, "prior_exercises": [], "gap_tags": []}
    finally:
        driver.close()


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/generate", summary="Generate targeted exercises for a specific concept")
async def generate_exercises(body: ExerciseGenerateRequest) -> dict[str, Any]:
    """
    Generate 3 practice exercises for a specific standard/concept on demand.
    Called by the frontend when the AI Tutor recommends practice.
    Reuses the same Gemini prompt as remediation_agent so exercises are
    personalised to the student's θ and avoid repeating prior questions.
    """
    from backend.app.agents.vertex_llm import get_llm

    loop = asyncio.get_event_loop()

    # 1. Fetch student context (θ, p_mastery, history) from Neo4j
    ctx = await loop.run_in_executor(
        None,
        lambda: _fetch_student_context(body.student_id, body.node_identifier),
    )

    theta     = ctx["theta"]
    p_mastery = ctx["p_mastery"]
    prior     = ctx["prior_exercises"]
    gap_tags  = ctx.get("gap_tags") or []

    grade_label  = f"Grade {body.grade.replace('K', '')}"
    subject_name = "Mathematics" if body.subject.lower() == "math" else "English Language Arts"

    # Determine exercise type from mastery level
    if p_mastery < 0.25:
        exercise_type = "foundational re-teaching with concrete examples"
        dok_target = 1
    elif p_mastery < 0.45:
        exercise_type = "guided practice with scaffolding"
        dok_target = 2
    else:
        exercise_type = "application and problem-solving"
        dok_target = 2

    nanopoint_tag = (
        f"[NanoPoint_ID: {body.node_identifier} | "
        f"Standard: {body.standard_code} | Difficulty: {p_mastery:.2f} | DOK: {dok_target}]"
    )

    # Build exercise memory block to avoid repetition
    memory_block = ""
    if prior:
        seen = [
            f"  - {'✓' if e.get('correct') else '✗'} DOK{e.get('dok_level', 2)}: {str(e.get('question_text', ''))[:100]}"
            for e in prior
        ]
        correct_count = sum(1 for e in prior if e.get("correct"))
        memory_block = (
            f"\nStudent's prior exercises for this standard ({correct_count}/{len(prior)} correct):\n"
            + "\n".join(seen)
            + "\nIMPORTANT: Do NOT repeat or closely paraphrase the above. "
            "Generate exercises that approach the concept from a DIFFERENT angle."
        )
        if prior and correct_count / len(prior) < 0.4:
            memory_block += (
                "\nThe student has struggled across multiple sessions — "
                "try a more concrete, visual, or real-world framing."
            )

    # Build sub-skill gap block from persisted gap_tags
    # gap_tags is a deduped list (max 5) of specific sub-skill labels detected by
    # the Dynamic Weight Auditor during prior exercise sessions, e.g.
    # ["regrouping", "place value", "denominator vs numerator"]
    gap_tag_block = ""
    if gap_tags:
        from collections import Counter
        tag_counts = Counter(gap_tags)
        tag_lines = [
            f"  • {tag} (flagged {count}x)" if count > 1 else f"  • {tag}"
            for tag, count in tag_counts.most_common()
        ]
        gap_tag_block = (
            "\nDetected sub-skill gaps from live practice monitoring:\n"
            + "\n".join(tag_lines)
            + "\nCRITICAL: Your exercises MUST specifically target these sub-skills. "
            "Each exercise should directly address at least one of these gaps. "
            "Do NOT generate generic exercises — the student is stuck on these exact sub-skills."
        )

    concept_label = body.concept or f"{body.standard_code} — {subject_name}"

    prompt = f"""You are a remediation specialist creating targeted practice exercises for a {grade_label} {subject_name} student.
{nanopoint_tag}

Student ability θ = {theta:+.2f} (0 = average; negative = struggling; positive = strong)
Concept: {concept_label}
Current mastery probability: {p_mastery:.0%}
Exercise focus: {exercise_type} (DOK {dok_target}){memory_block}{gap_tag_block}

Create exactly 3 practice exercises that:
1. Are age-appropriate for {grade_label}
2. Specifically address '{concept_label}'
3. Progress from easier to harder (exercise 1 → 3)
4. Include a brief concept explanation per exercise
5. Use real-world {subject_name} scenarios
6. Are DIFFERENT from any exercises the student has already seen

Return ONLY a valid JSON object:
{{
  "concept_explanation": "<2-sentence plain-English explanation for the student>",
  "exercises": [
    {{
      "order": 1,
      "type": "practice|word_problem|visual|computation",
      "question": "<exercise text>",
      "hint": "<one-sentence hint>",
      "answer": "<correct answer with working>",
      "explanation": "<why this is the answer>",
      "dok_level": {dok_target}
    }}
  ]
}}"""

    llm = get_llm()
    try:
        raw = await loop.run_in_executor(None, lambda: llm.generate_json(prompt))
    except Exception as exc:
        logger.error(f"exercises/generate LLM call failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Exercise generation failed: {exc}")

    # generate_json auto-unwraps {"exercises": [...]} to a plain list —
    # handle both the unwrapped list and the full dict form.
    if isinstance(raw, list) and raw:
        exercises_list = raw
        concept_explanation = ""
    elif isinstance(raw, dict) and raw.get("exercises"):
        exercises_list = raw["exercises"]
        concept_explanation = raw.get("concept_explanation", "")
    else:
        logger.error(f"exercises/generate: unexpected LLM output: {type(raw)} {str(raw)[:200]}")
        raise HTTPException(status_code=500, detail="LLM returned empty exercise set")

    return {
        "node_identifier":     body.node_identifier,
        "standard_code":       body.standard_code,
        "concept":             concept_label,
        "nanopoint_tag":       nanopoint_tag,
        "concept_explanation": concept_explanation,
        "exercises":           exercises_list,
        "student_theta":       theta,
        "p_mastery":           p_mastery,
        "dok_target":          dok_target,
    }


# ── Practice queue + readiness ─────────────────────────────────────────────────

@router.get("/queue/{student_id}", summary="Get prioritised gap-based practice queue")
async def exercise_queue(
    student_id: str,
    grade: str = "all",
    subject: str = "all",
    limit: int = 8,
) -> dict:
    """
    Returns a priority-ordered list of the student's knowledge gaps as
    ready-to-launch exercise cards, plus a re-assessment readiness signal.

    Ordering: hard-blocked first (failure_streak >= 3), then by how many
    downstream concepts the gap blocks, then by lowest mastery.

    Re-assessment readiness: student has practised >= 3 skills to mastery
    (attempts >= 2 AND mastery >= 0.65), suggesting the formal exam would
    now show real improvement.
    """
    loop = asyncio.get_event_loop()

    def _fetch() -> dict:
        driver = _neo4j()
        try:
            with driver.session(database=settings.neo4j_database) as session:
                grade_filter = (
                    "" if grade == "all"
                    else "AND n.gradeLevelList IS NOT NULL AND ANY(g IN n.gradeLevelList WHERE g = $grade_num)"
                )
                subj_filter = (
                    "" if subject == "all"
                    else "AND toLower(n.academicSubject) CONTAINS toLower($subj)"
                )
                grade_num = grade.replace("K", "") if grade != "all" else ""

                # ── Gap queue with downstream impact ─────────────────────────
                gap_rows = session.run(
                    f"""
                    MATCH (s:Student {{id: $sid}})-[r:SKILL_STATE]->(n:StandardsFrameworkItem)
                    WHERE r.p_mastery IS NOT NULL AND r.p_mastery < 0.55
                      AND n.statementCode IS NOT NULL AND n.description IS NOT NULL
                      AND NOT n.statementCode STARTS WITH 'node'
                      {grade_filter}
                      {subj_filter}
                    OPTIONAL MATCH (n)-[:PRECEDES|BUILDS_TOWARDS*1..3]->(ds:StandardsFrameworkItem)
                    RETURN n.identifier        AS identifier,
                           n.statementCode     AS code,
                           n.description       AS description,
                           n.gradeLevelList     AS grades,
                           n.academicSubject    AS subject_area,
                           r.p_mastery         AS mastery,
                           coalesce(r.attempts, 0)       AS attempts,
                           coalesce(r.failure_streak, 0) AS failure_streak,
                           count(DISTINCT ds)  AS downstream_count
                    ORDER BY r.p_mastery ASC
                    LIMIT $lim
                    """,
                    sid=student_id,
                    grade_num=grade_num,
                    subj=subject,
                    lim=limit * 3,  # over-fetch so we can re-sort
                ).data()

                # ── Re-assessment readiness ───────────────────────────────────
                readiness_row = session.run(
                    """
                    MATCH (s:Student {id: $sid})-[r:SKILL_STATE]->(n:StandardsFrameworkItem)
                    WHERE r.p_mastery >= 0.65
                      AND coalesce(r.attempts, 0) >= 2
                      AND n.statementCode IS NOT NULL
                      AND NOT n.statementCode STARTS WITH 'node'
                    RETURN count(*) AS improved_count
                    """,
                    sid=student_id,
                ).single()
                improved_count = int(readiness_row["improved_count"]) if readiness_row else 0

                return {"gaps": gap_rows, "improved_count": improved_count}
        finally:
            driver.close()

    try:
        data = await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        logger.warning(f"exercise_queue failed for {student_id}: {exc}")
        return {
            "student_id":              student_id,
            "queue":                   [],
            "ready_for_reassessment":  False,
            "improved_count":          0,
            "reassessment_threshold":  3,
        }

    gaps = data["gaps"]

    # Re-sort: hard-blocked (streak >= 3) first, then downstream impact, then mastery
    gaps.sort(key=lambda g: (
        0 if g["failure_streak"] >= 3 else 1,
        -(g["downstream_count"] or 0),
        float(g["mastery"] or 1.0),
    ))
    gaps = gaps[:limit]

    queue = []
    for rank, g in enumerate(gaps, 1):
        grade_list = g.get("grades") or ["?"]
        item_grade = f"K{grade_list[0]}" if grade_list[0] != "?" else grade
        queue.append({
            "rank":             rank,
            "node_identifier":  g["identifier"],
            "code":             g["code"],
            "description":      g["description"],
            "grade":            item_grade,
            "subject":          g.get("subject_area") or subject,
            "mastery":          round(float(g["mastery"] or 0), 3),
            "attempts":         int(g["attempts"] or 0),
            "downstream_count": int(g["downstream_count"] or 0),
            "priority": (
                "high"   if g["failure_streak"] >= 3 else
                "medium" if (g["downstream_count"] or 0) >= 5 else
                "low"
            ),
        })

    reassessment_threshold = 3
    return {
        "student_id":             student_id,
        "queue":                  queue,
        "ready_for_reassessment": data["improved_count"] >= reassessment_threshold,
        "improved_count":         data["improved_count"],
        "reassessment_threshold": reassessment_threshold,
    }
