"""
Rasch-Staircase Assessment API.

Endpoints:
  POST /rasch/start                   — initialise session (grade → θ₀)
  GET  /rasch/{session_id}/next       — select next node + generate question via Gemini
  POST /rasch/{session_id}/answer     — submit answer, update θ, update mastery
  GET  /rasch/{session_id}/heatmap    — finalise session, return heat-map propagation
  GET  /rasch/{session_id}/status     — current θ, q_count, status
"""

from __future__ import annotations

import asyncio
import random
from functools import partial
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/rasch", tags=["Rasch Adaptive Assessment"])


# ── Request / Response models ────────────────────────────────────────────────

class StartRequest(BaseModel):
    student_id: str = Field(default="student_001")
    grade: int = Field(..., ge=1, le=8, description="Grade level 1-8")


class AnswerRequest(BaseModel):
    node_id:      str   = Field(..., description="StandardsFrameworkItem identifier")
    is_correct:   bool
    time_seconds: float = Field(default=60.0, ge=0)
    node_weights: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "For multi-standard questions: [{node_id, weight}, ...]. "
            "Weights are normalised to sum=1. Omit for single-standard."
        ),
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/start", summary="Initialise a Rasch adaptive session")
async def start_session(body: StartRequest) -> dict[str, Any]:
    """
    Create a new Rasch session for a student.

    - Sets θ₀ = grade level (Grade 4 → 4.0)
    - Persists session in Neo4j
    - Returns session_id to use in subsequent calls
    """
    try:
        from backend.app.student.rasch_engine import RaschEngine
        engine = RaschEngine()
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            partial(engine.start_session, body.student_id, body.grade),
        )
        engine.close()
        return result
    except Exception as exc:
        logger.error(f"Rasch start failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{session_id}/next", summary="Get next adaptive question")
async def next_question(session_id: str) -> dict[str, Any]:
    """
    Select the next StandardsFrameworkItem (β ≈ θ) and generate a question via Gemini.

    - Returns the selected standard node + a Gemini-generated MC question
    - Returns {done: true} when all 15 questions are complete
    """
    try:
        from backend.app.student.rasch_engine import RaschEngine
        from backend.app.llm.gemini_service import GeminiService

        engine = RaschEngine()
        loop   = asyncio.get_event_loop()
        node   = await loop.run_in_executor(
            None, partial(engine.select_next_node, session_id)
        )
        engine.close()

        if node is None:
            return {"done": True, "message": "Assessment complete. Call /heatmap to finalise."}

        # Generate question via Gemini
        question = await _generate_question_for_node(node)

        return {
            "done":     False,
            "node_id":  node["identifier"],
            "standard": {
                "code":        node.get("code"),
                "description": node.get("description"),
                "grade_level": node.get("gradeLevel"),
                "difficulty":  node.get("difficulty"),
                "jurisdiction": node.get("jurisdiction"),
            },
            "question": question,
        }
    except Exception as exc:
        logger.error(f"Rasch next question failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{session_id}/answer", summary="Submit an answer and update θ")
async def record_answer(session_id: str, body: AnswerRequest) -> dict[str, Any]:
    """
    Record a student answer and update the Rasch ability estimate θ.

    - Rasch 1PL: θ_new = θ + K*(outcome - P(correct))
    - K = 1.2 for Q1-Q5, K = 0.6 for Q6-Q15
    - Time bonus: +0.15 if correct AND time ≤ 30s AND β > θ
    - Multi-standard: supply node_weights for proportional mastery credit

    Returns updated θ, delta, and mastery updates written to Neo4j.
    """
    try:
        from backend.app.student.rasch_engine import RaschEngine
        engine = RaschEngine()
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            partial(
                engine.record_answer,
                session_id,
                body.node_id,
                body.is_correct,
                body.time_seconds,
                body.node_weights,
            ),
        )
        engine.close()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Rasch answer failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{session_id}/heatmap", summary="Finalise session and return mastery heat-map")
async def get_heatmap(session_id: str) -> dict[str, Any]:
    """
    Finalise the Rasch session and propagate mastery through the knowledge graph.

    **Frontier discovery**: all standards with β ≤ θ_final → p_mastery = 0.85
    **Ancestor sweep**: follow BUILDS_TOWARDS backward → p_mastery = 0.98
    **Future path**: follow BUILDS_TOWARDS forward → next best actions for the student

    All SKILL_STATE edges are upserted in Neo4j (only raises mastery, never lowers).
    """
    try:
        from backend.app.student.rasch_engine import RaschEngine
        engine = RaschEngine()
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, partial(engine.finalize_session, session_id)
        )
        engine.close()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Rasch heatmap failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{session_id}/status", summary="Current session state")
async def session_status(session_id: str) -> dict[str, Any]:
    """Return current θ, q_count, and status for an active session."""
    try:
        from neo4j import GraphDatabase
        from backend.app.core.settings import settings

        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        with driver.session(database=settings.neo4j_database) as s:
            row = s.run(
                """
                MATCH (rs:RaschSession {session_id: $sid})
                RETURN rs.theta AS theta, rs.q_count AS q_count,
                       rs.status AS status, rs.grade AS grade,
                       rs.student_id AS student_id
                """,
                sid=session_id,
            ).single()
        driver.close()

        if not row:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        return {
            "session_id":      session_id,
            "student_id":      row["student_id"],
            "grade":           row["grade"],
            "theta":           round(float(row["theta"]), 3),
            "q_count":         row["q_count"],
            "questions_left":  max(0, 15 - int(row["q_count"])),
            "status":          row["status"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Rasch status failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ── Fallback question bank (used when Gemini is unavailable) ──────────────────

_GRADE_FALLBACKS: dict[tuple[int, int], list[tuple]] = {
    (1, 2): [
        ("What is 8 + 7?",
         ["A. 14", "B. 15", "C. 16", "D. 13"], "B", 1),
        ("Which number is 10 more than 34?",
         ["A. 24", "B. 40", "C. 44", "D. 43"], "C", 1),
        ("Sam has 12 apples. She gives away 5. How many does she have left?",
         ["A. 6", "B. 7", "C. 8", "D. 17"], "B", 2),
        ("Which of these shows 3 tens and 4 ones?",
         ["A. 43", "B. 34", "C. 304", "D. 40"], "B", 1),
    ],
    (3, 4): [
        ("What is 7 × 8?",
         ["A. 54", "B. 56", "C. 58", "D. 52"], "B", 1),
        ("Which fraction is equivalent to 1/2?",
         ["A. 2/3", "B. 3/4", "C. 4/8", "D. 3/5"], "C", 1),
        ("Round 347 to the nearest hundred.",
         ["A. 300", "B. 350", "C. 400", "D. 340"], "C", 1),
        ("A bag has 24 pencils split equally into 4 groups. How many are in each group?",
         ["A. 4", "B. 5", "C. 6", "D. 8"], "C", 2),
    ],
    (5, 6): [
        ("What is 25% of 80?",
         ["A. 15", "B. 20", "C. 25", "D. 30"], "B", 1),
        ("Solve for x: 3x + 6 = 21",
         ["A. x = 4", "B. x = 5", "C. x = 6", "D. x = 7"], "B", 2),
        ("What is the area of a rectangle with length 9 cm and width 6 cm?",
         ["A. 30 cm²", "B. 48 cm²", "C. 54 cm²", "D. 15 cm²"], "C", 1),
        ("Which ratio is equivalent to 3:4?",
         ["A. 6:10", "B. 9:12", "C. 4:3", "D. 6:9"], "B", 1),
    ],
    (7, 8): [
        ("Simplify: 2(3x − 4) + 5x",
         ["A. 11x − 4", "B. 11x − 8", "C. 6x − 4", "D. 11x + 8"], "B", 2),
        ("What is the slope of the line y = −2x + 5?",
         ["A. 5", "B. 2", "C. −2", "D. −5"], "C", 1),
        ("The ratio of boys to girls is 3:5. If there are 24 boys, how many students total?",
         ["A. 40", "B. 48", "C. 64", "D. 56"], "C", 2),
        ("A triangle has angles 90° and 35°. What is the third angle?",
         ["A. 35°", "B. 45°", "C. 55°", "D. 65°"], "C", 1),
    ],
}


def _fallback_question(node: dict[str, Any]) -> dict[str, Any]:
    """Return a grade-appropriate template question when Gemini is unavailable."""
    grade = int(float(node.get("difficulty") or 3))
    for (lo, hi), templates in _GRADE_FALLBACKS.items():
        if lo <= grade <= hi:
            q, opts, ans, dok = random.choice(templates)
            return {"question": q, "options": opts, "answer": ans,
                    "dok_level": dok, "dok_label": "Recall (fallback)"}
    q, opts, ans, dok = random.choice(_GRADE_FALLBACKS[(3, 4)])
    return {"question": q, "options": opts, "answer": ans,
            "dok_level": dok, "dok_label": "Recall (fallback)"}


# ── Analysis endpoint ──────────────────────────────────────────────────────────

@router.get("/{session_id}/analysis", summary="LLM analysis of student performance")
async def get_analysis(session_id: str) -> dict[str, Any]:
    """
    Generate a narrative diagnostic report for the teacher.

    Loads every answered question from Neo4j, builds a performance summary,
    and asks Gemini to identify strengths, gaps, and recommendations.
    Falls back to a rule-based summary when Gemini is unavailable.
    """
    try:
        from neo4j import GraphDatabase
        from backend.app.core.settings import settings
        from backend.app.llm.gemini_service import GeminiService
        from backend.app.student.rasch_engine import _theta_label

        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        with driver.session(database=settings.neo4j_database) as s:
            sess_row = s.run(
                """
                MATCH (rs:RaschSession {session_id: $sid})
                RETURN rs.theta AS theta, rs.grade AS grade,
                       rs.q_count AS q_count, rs.student_id AS student_id
                """,
                sid=session_id,
            ).single()
            if not sess_row:
                raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

            answered_rows = s.run(
                """
                MATCH (rs:RaschSession {session_id: $sid})-[a:ANSWERED]->(n:StandardsFrameworkItem)
                RETURN n.statementCode AS code,
                       n.description   AS description,
                       n.difficulty    AS beta,
                       a.is_correct    AS is_correct,
                       a.theta_before  AS theta_before,
                       a.theta_after   AS theta_after,
                       a.time_seconds  AS time_seconds,
                       a.q_number      AS q_number
                ORDER BY a.q_number ASC
                """,
                sid=session_id,
            )
            answered = [dict(r) for r in answered_rows]
        driver.close()

        theta_final  = float(sess_row["theta"])
        grade        = int(sess_row["grade"])
        q_count      = int(sess_row["q_count"])
        ability      = _theta_label(theta_final)
        correct_count = sum(1 for a in answered if a["is_correct"])

        if not answered:
            return {
                "session_id": session_id,
                "analysis": (
                    f"No questions were answered. Grade {grade} student, "
                    f"θ = {theta_final:.2f}."
                ),
                "correct": 0,
                "total": q_count,
            }

        # Build readable performance table for the prompt
        perf_lines = []
        for a in answered:
            status     = "CORRECT  " if a["is_correct"] else "INCORRECT"
            desc       = (a.get("description") or "")[:90]
            theta_diff = float(a.get("theta_after") or 0) - float(a.get("theta_before") or 0)
            perf_lines.append(
                f"  Q{a['q_number']:>2}: {status} | [{a.get('code') or '—':>12}] "
                f"β={float(a.get('beta') or 0):.1f}  Δθ={theta_diff:+.3f}\n"
                f"         {desc}..."
            )

        prompt = (
            "You are an educational diagnostics expert reviewing a K–8 math assessment.\n\n"
            f"Student Grade: {grade}  |  Final ability θ = {theta_final:.2f} ({ability})\n"
            f"Score: {correct_count}/{q_count} correct  "
            f"({100*correct_count//max(q_count,1)}%)\n\n"
            "Questions answered (β = item difficulty, Δθ = ability change per question):\n"
            + "\n".join(perf_lines) +
            "\n\n"
            "Write a diagnostic report in exactly THREE clearly labelled paragraphs:\n"
            "**OVERALL PERFORMANCE**: Describe the student's current ability level and "
            "what it means for their grade placement.\n"
            "**STRENGTHS**: Name the specific standards/topics where they demonstrated "
            "competence (cite standard codes for correct answers, especially hard ones).\n"
            "**GAPS & RECOMMENDATIONS**: Name the specific standards they missed, "
            "explain the likely misconception, and give the teacher one concrete "
            "next-day activity to address each gap.\n\n"
            "Write for a teacher audience. Be specific, cite standard codes. "
            "End with one sentence the teacher can act on tomorrow."
        )

        svc  = GeminiService()
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, svc.generate_content, prompt)

        if text:
            analysis = text.strip()
        else:
            # Rule-based fallback when Gemini is unavailable
            wrong = [a for a in answered if not a["is_correct"]]
            right = [a for a in answered if a["is_correct"]]
            strong_codes = ", ".join(
                a["code"] for a in right[:4] if a.get("code")
            ) or "—"
            gap_codes = ", ".join(
                a["code"] for a in wrong[:4] if a.get("code")
            ) or "—"
            analysis = (
                f"**OVERALL PERFORMANCE**: This Grade {grade} student achieved an ability "
                f"estimate of θ = {theta_final:.2f} ({ability}), answering "
                f"{correct_count} of {q_count} questions correctly.\n\n"
                f"**STRENGTHS**: The student demonstrated competence in: {strong_codes}.\n\n"
                f"**GAPS & RECOMMENDATIONS**: The student struggled with: {gap_codes}. "
                f"Recommend targeted practice on Grade {grade} standards at difficulty "
                f"β ≈ {theta_final:.1f}. Start with the easiest missed standard and "
                "build up using the Next Learning Targets shown above."
            )

        return {
            "session_id": session_id,
            "analysis":   analysis,
            "correct":    correct_count,
            "total":      q_count,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Rasch analysis failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ── Gemini question generation helper ─────────────────────────────────────────

async def _generate_question_for_node(node: dict[str, Any]) -> dict[str, Any] | None:
    """Generate a single MC question for a StandardsFrameworkItem via Gemini."""
    try:
        from backend.app.llm.gemini_service import GeminiService
        from backend.app.student.assessment_engine import DOK_LEVELS

        beta  = float(node.get("difficulty") or 3)
        grade = int(beta)
        dok   = 1 if beta <= 2 else (2 if beta <= 5 else 3)

        prompt = (
            f"Generate exactly ONE multiple-choice math question.\n\n"
            f"Standard: {node.get('code', '')}\n"
            f"Description: {node.get('description', '')}\n"
            f"Grade: Grade {grade} | Difficulty β={beta:.1f} | DOK {dok}\n\n"
            "Requirements:\n"
            "- A real math problem, NOT a meta-question about standards\n"
            f"- Age-appropriate for Grade {grade}\n"
            "- Exactly 4 options (A, B, C, D) with ONE correct answer\n"
            "- Plausible distractors targeting common misconceptions\n\n"
            "Return ONLY valid JSON — no markdown:\n"
            '{"question":"...","options":["A. ...","B. ...","C. ...","D. ..."],'
            f'"answer":"A","dok_level":{dok}}}'
        )

        svc  = GeminiService()
        text = await asyncio.get_event_loop().run_in_executor(
            None, svc.generate_content, prompt
        )
        if not text:
            return None

        parsed = svc.parse_json_response(text, array=False)
        if (parsed and parsed.get("question") and
                isinstance(parsed.get("options"), list) and
                len(parsed["options"]) == 4 and parsed.get("answer")):
            parsed["dok_label"] = DOK_LEVELS.get(dok, "")
            return parsed

    except Exception as exc:
        logger.warning(f"Gemini question generation failed for node: {exc}")

    # Fallback: return a grade-appropriate template question so the assessment
    # can always proceed even when Gemini is unavailable or returns invalid JSON.
    logger.info(f"Using fallback question for node difficulty={node.get('difficulty')}")
    return _fallback_question(node)
