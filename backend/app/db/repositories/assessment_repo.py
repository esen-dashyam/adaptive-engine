"""Assessment session and answer repository."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.db.models.assessment import AssessmentAnswer, AssessmentSession


class AssessmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_session(
        self,
        student_id: uuid.UUID,
        grade: str,
        subject: str,
        framework: str,
        state_jurisdiction: str,
        num_questions: int,
    ) -> AssessmentSession:
        sess = AssessmentSession(
            student_id=student_id,
            grade=grade,
            subject=subject,
            framework=framework,
            state_jurisdiction=state_jurisdiction,
            num_questions=num_questions,
            phase="in_progress",
        )
        self.session.add(sess)
        await self.session.flush()
        return sess

    async def get_session(self, session_id: uuid.UUID) -> AssessmentSession | None:
        result = await self.session.execute(
            select(AssessmentSession)
            .options(selectinload(AssessmentSession.answers))
            .where(AssessmentSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def save_answer(
        self,
        session_id: uuid.UUID,
        question_id: str,
        question_text: str,
        standard_code: str,
        category: str,
        dok_level: int,
        student_answer: str,
        correct_answer: str,
        is_correct: bool,
        mastery_before: float,
        mastery_after: float,
    ) -> AssessmentAnswer:
        answer = AssessmentAnswer(
            session_id=session_id,
            question_id=question_id,
            question_text=question_text,
            standard_code=standard_code,
            category=category,
            dok_level=dok_level,
            student_answer=student_answer,
            correct_answer=correct_answer,
            is_correct=is_correct,
            mastery_before=mastery_before,
            mastery_after=mastery_after,
        )
        self.session.add(answer)
        await self.session.flush()
        return answer

    async def finalize_session(
        self,
        session_id: uuid.UUID,
        score: float,
        gap_analysis: dict,
        remediation_plan: dict,
        phase: str = "done",
    ) -> AssessmentSession:
        sess = await self.get_session(session_id)
        assert sess is not None
        sess.score = score
        sess.gap_analysis = gap_analysis
        sess.remediation_plan = remediation_plan
        sess.phase = phase
        sess.completed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return sess

    async def list_sessions_for_student(
        self, student_id: uuid.UUID, limit: int = 20
    ) -> list[AssessmentSession]:
        result = await self.session.execute(
            select(AssessmentSession)
            .where(AssessmentSession.student_id == student_id)
            .order_by(AssessmentSession.started_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
