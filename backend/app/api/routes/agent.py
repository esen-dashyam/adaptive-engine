"""
Adaptive Assessment Agent — REST endpoints.

Two-phase API:

  Phase A — Start assessment (select standards + generate questions):
    POST /api/v1/agent/assess/start
    Body: {student_id, grade, subject, framework, state_jurisdiction}
    Returns: {session_id, questions: [...]}

  Phase B — Submit answers (evaluate + BKT + gap analysis + remediation):
    POST /api/v1/agent/assess/submit
    Body: {session_id, student_id, grade, subject, framework,
           state_jurisdiction, questions, answers: [...],
           pg_session_id, pg_student_uuid}
    Returns: {score, mastery_updates, gaps, remediation_plan, phase}

  GET  /api/v1/agent/sessions/{student_id}     — list past sessions
  GET  /api/v1/agent/mastery/{student_id}       — current mastery profile
  GET  /api/v1/agent/gaps/{student_id}          — current gap report
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.graph import get_phase_a, get_phase_b
from backend.app.agent.state import AssessmentState
from backend.app.core.settings import settings
from backend.app.db.engine import get_async_session
from backend.app.db.repositories.assessment_repo import AssessmentRepository
from backend.app.db.repositories.mastery_repo import MasteryRepository
from backend.app.db.repositories.student_repo import StudentRepository

router = APIRouter(prefix="/agent", tags=["Adaptive Agent"])


# ── Request / Response models ─────────────────────────────────────────────────

class StartRequest(BaseModel):
    student_id: str
    grade: str
    subject: str = "Mathematics"
    framework: str = "CCSS"
    state_jurisdiction: str = "Multi-State"


class StartResponse(BaseModel):
    session_id: str
    pg_session_id: str
    pg_student_uuid: str
    questions: list[dict[str, Any]]
    standards_selected: int
    phase: str


class AnswerItem(BaseModel):
    question_id: str
    selected_answer: str


class SubmitRequest(BaseModel):
    session_id: str
    pg_session_id: str
    pg_student_uuid: str
    student_id: str
    grade: str
    subject: str
    framework: str
    state_jurisdiction: str
    questions: list[dict[str, Any]]
    all_nodes: list[dict[str, Any]]
    mastery_updates_prior: dict[str, float] = {}
    answers: list[AnswerItem]


class SubmitResponse(BaseModel):
    session_id: str
    pg_session_id: str
    score: float
    correct_count: int
    total_questions: int
    mastery_updates: dict[str, float]
    gaps: list[dict[str, Any]]
    remediation_plan: list[dict[str, Any]]
    phase: str


# ── Phase A — Start ────────────────────────────────────────────────────────────

@router.post("/assess/start", response_model=StartResponse)
async def start_assessment(
    req: StartRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Phase A: select standards from Neo4j KG → fetch GraphRAG context → generate questions.

    Creates a Postgres AssessmentSession and Student row (if new).
    Returns the generated questions so the frontend can render the quiz.
    """
    student_repo = StudentRepository(db)
    assess_repo = AssessmentRepository(db)

    # Ensure student row exists
    student = await student_repo.get_or_create(
        external_id=req.student_id,
        grade=req.grade,
    )

    # Create a Postgres session row immediately (phase=in_progress)
    pg_sess = await assess_repo.create_session(
        student_id=student.id,
        grade=req.grade,
        subject=req.subject,
        framework=req.framework,
        state_jurisdiction=req.state_jurisdiction,
        num_questions=settings.agent_max_questions,
    )

    # Run Phase A LangGraph
    initial_state = AssessmentState(
        student_id=req.student_id,
        grade=req.grade,
        subject=req.subject,
        framework=req.framework,
        state_jurisdiction=req.state_jurisdiction,
        pg_session_id=str(pg_sess.id),
        pg_student_uuid=str(student.id),
        phase="start",
    )

    try:
        graph = get_phase_a()
        final_state_dict = graph.invoke(initial_state.model_dump())
        final_state = AssessmentState(**final_state_dict)
    except Exception as exc:
        logger.error(f"[start_assessment] Phase A graph error: {exc}")
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    if final_state.error:
        raise HTTPException(status_code=500, detail=final_state.error)

    if not final_state.questions:
        raise HTTPException(
            status_code=503,
            detail="No questions generated — check Gemini API key and Neo4j data",
        )

    session_id = str(uuid.uuid4())

    return StartResponse(
        session_id=session_id,
        pg_session_id=str(pg_sess.id),
        pg_student_uuid=str(student.id),
        questions=final_state.questions,
        standards_selected=len(final_state.all_nodes),
        phase=final_state.phase,
    )


# ── Phase B — Submit ──────────────────────────────────────────────────────────

@router.post("/assess/submit", response_model=SubmitResponse)
async def submit_answers(req: SubmitRequest):
    """
    Phase B: evaluate answers → BKT update → strong gap Cypher query →
             Gemini remediation → persist everything to Postgres.

    Returns the full assessment report including per-standard mastery updates,
    gap analysis, and a personalised remediation plan.
    """
    state = AssessmentState(
        student_id=req.student_id,
        grade=req.grade,
        subject=req.subject,
        framework=req.framework,
        state_jurisdiction=req.state_jurisdiction,
        questions=req.questions,
        all_nodes=req.all_nodes,
        submitted_answers=[a.model_dump() for a in req.answers],
        pg_session_id=req.pg_session_id,
        pg_student_uuid=req.pg_student_uuid,
        phase="evaluate",
    )

    try:
        graph = get_phase_b()
        final_dict = graph.invoke(state.model_dump())
        final = AssessmentState(**final_dict)
    except Exception as exc:
        logger.error(f"[submit_answers] Phase B graph error: {exc}")
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    correct_count = sum(1 for r in final.results if r.get("is_correct"))

    return SubmitResponse(
        session_id=req.session_id,
        pg_session_id=req.pg_session_id,
        score=final.score,
        correct_count=correct_count,
        total_questions=len(final.results),
        mastery_updates=final.mastery_updates,
        gaps=final.gaps,
        remediation_plan=final.remediation_plan,
        phase=final.phase,
    )


# ── History & Profile ─────────────────────────────────────────────────────────

@router.get("/sessions/{student_id}")
async def get_sessions(
    student_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_async_session),
):
    """List past assessment sessions for a student."""
    student_repo = StudentRepository(db)
    student = await student_repo.get_by_external_id(student_id)
    if not student:
        return {"sessions": []}

    assess_repo = AssessmentRepository(db)
    sessions = await assess_repo.list_sessions_for_student(student.id, limit=limit)

    return {
        "student_id": student_id,
        "sessions": [
            {
                "id": str(s.id),
                "grade": s.grade,
                "subject": s.subject,
                "score": s.score,
                "phase": s.phase,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "gap_count": len((s.gap_analysis or {}).get("gaps", [])),
            }
            for s in sessions
        ],
    }


@router.get("/mastery/{student_id}")
async def get_mastery_profile(
    student_id: str,
    subject: str | None = None,
    grade: str | None = None,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Return the full Postgres mastery profile for a student.
    Shows all tracked standards with their BKT probability and attempt count.
    """
    student_repo = StudentRepository(db)
    student = await student_repo.get_by_external_id(student_id)
    if not student:
        return {"student_id": student_id, "mastery": []}

    mastery_repo = MasteryRepository(db)
    records = await mastery_repo.list_for_student(
        student.id, subject=subject, grade=grade
    )

    return {
        "student_id": student_id,
        "mastery": [
            {
                "standard_code": r.standard_code,
                "subject": r.subject,
                "grade": r.grade,
                "mastery_prob": r.mastery_prob,
                "attempts": r.attempts,
                "correct": r.correct,
                "last_assessed": r.last_assessed.isoformat() if r.last_assessed else None,
            }
            for r in records
        ],
    }


@router.get("/gaps/{student_id}")
async def get_gaps(
    student_id: str,
    subject: str | None = None,
    threshold: float = 0.7,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Return Postgres-backed gap report: standards below mastery threshold,
    plus Neo4j downstream blocking count for each.
    """
    student_repo = StudentRepository(db)
    student = await student_repo.get_by_external_id(student_id)
    if not student:
        return {"student_id": student_id, "gaps": []}

    mastery_repo = MasteryRepository(db)
    weak = await mastery_repo.get_gaps(student.id, threshold=threshold, subject=subject)

    if not weak:
        return {"student_id": student_id, "gaps": [], "threshold": threshold}

    # Enrich with downstream blocking count from Neo4j
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    codes = [r.standard_code for r in weak]
    blocking_map: dict[str, int] = {}
    try:
        with driver.session(database=settings.neo4j_database) as s:
            res = s.run(
                """
                UNWIND $codes AS code
                MATCH (src:StandardsFrameworkItem)
                WHERE src.identifier = code OR src.statementCode = code
                OPTIONAL MATCH (src)-[:buildsTowards|hasChild*1..3]->(d:StandardsFrameworkItem)
                WITH code, count(DISTINCT d) AS blocked
                RETURN code, blocked
                """,
                codes=codes,
            )
            for r in res:
                blocking_map[r["code"]] = r["blocked"]
    except Exception as exc:
        logger.warning(f"[get_gaps] Neo4j enrichment failed: {exc}")
    finally:
        driver.close()

    return {
        "student_id": student_id,
        "threshold": threshold,
        "gaps": [
            {
                "standard_code": r.standard_code,
                "subject": r.subject,
                "grade": r.grade,
                "mastery_prob": r.mastery_prob,
                "attempts": r.attempts,
                "blocked_downstream": blocking_map.get(r.standard_code, 0),
            }
            for r in sorted(
                weak,
                key=lambda x: blocking_map.get(x.standard_code, 0),
                reverse=True,
            )
        ],
    }
