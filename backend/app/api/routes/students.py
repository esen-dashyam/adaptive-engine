"""Student management and skill profile routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/students", tags=["Students"])


class StudentCreate(BaseModel):
    student_id: str = Field(..., description="Unique student identifier")
    name: str = Field(default="")
    grade: str = Field(default="K1", description="Current grade: K1-K8")
    state: str = Field(default="Multi-State")


@router.post("/", summary="Register a new student")
async def create_student(body: StudentCreate) -> dict[str, Any]:
    """Create a Student node in Neo4j."""
    try:
        from neo4j import GraphDatabase
        from backend.app.core.settings import settings

        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        with driver.session(database=settings.neo4j_database) as session:
            session.run("""
                MERGE (s:Student {student_id: $sid})
                ON CREATE SET s.name = $name, s.grade = $grade, s.state = $state,
                              s.created_at = datetime()
                ON MATCH  SET s.name = $name, s.grade = $grade, s.state = $state,
                              s.updated_at = datetime()
            """, sid=body.student_id, name=body.name, grade=body.grade, state=body.state)
        driver.close()
        return {"student_id": body.student_id, "name": body.name,
                "grade": body.grade, "state": body.state, "status": "created"}
    except Exception as exc:
        logger.error(f"Create student failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{student_id}", summary="Get student skill profile")
async def get_student(student_id: str) -> dict[str, Any]:
    """Return the student's full BKT skill profile."""
    try:
        from backend.app.student.bayesian_tracker import BayesianSkillTracker

        tracker = BayesianSkillTracker()
        profile = tracker.get_skill_profile(student_id)
        tracker.close()
        return profile
    except Exception as exc:
        logger.error(f"Get student failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{student_id}/gaps", summary="Find blocking knowledge gaps")
async def student_gaps(
    student_id: str,
    subject: str = Query(None, description="Filter by subject: math or english"),
) -> dict[str, Any]:
    """Return standards where low mastery blocks the most downstream concepts."""
    try:
        from backend.app.student.bayesian_tracker import BayesianSkillTracker

        subject_name = None
        if subject:
            subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"

        tracker = BayesianSkillTracker()
        gaps    = tracker.find_blocking_gaps(student_id, subject=subject_name)
        tracker.close()
        return {"student_id": student_id, "blocking_gaps": gaps, "gap_count": len(gaps)}
    except Exception as exc:
        logger.error(f"Student gaps failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{student_id}/nano-weights", summary="Nano weights for a grade/subject")
async def nano_weights(
    student_id: str,
    grade:   str = Query(..., description="Grade: K1-K8"),
    subject: str = Query(..., description="Subject: math or english"),
) -> dict[str, Any]:
    """Return nano weight (0-100) for every standard in the given grade and subject."""
    try:
        from backend.app.student.bayesian_tracker import BayesianSkillTracker

        tracker = BayesianSkillTracker()
        weights = tracker.get_nano_weights_for_grade(student_id, grade, subject)
        tracker.close()

        avg_weight = (
            round(sum(w["nano_weight"] for w in weights) / len(weights), 1) if weights else 0
        )
        return {
            "student_id": student_id, "grade": grade, "subject": subject,
            "standards_count": len(weights), "avg_nano_weight": avg_weight,
            "nano_weights": weights,
        }
    except Exception as exc:
        logger.error(f"Nano weights failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
