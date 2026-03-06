"""
Mastery repository — Postgres audit copy of BKT mastery probabilities.

Neo4j SkillState is the primary source. This table is the secondary copy
written after every assessment evaluation for analytics and reporting.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.student import MasteryRecord


class MasteryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, student_id: uuid.UUID, standard_code: str) -> MasteryRecord | None:
        result = await self.session.execute(
            select(MasteryRecord).where(
                MasteryRecord.student_id == student_id,
                MasteryRecord.standard_code == standard_code,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        student_id: uuid.UUID,
        standard_code: str,
        is_correct: bool,
        mastery_prob: float,
        subject: str | None = None,
        grade: str | None = None,
    ) -> MasteryRecord:
        record = await self.get(student_id, standard_code)
        if record is None:
            record = MasteryRecord(
                student_id=student_id,
                standard_code=standard_code,
                subject=subject,
                grade=grade,
            )
            self.session.add(record)

        record.attempts += 1
        if is_correct:
            record.correct += 1
        record.mastery_prob = mastery_prob
        record.last_assessed = datetime.now(timezone.utc)
        await self.session.flush()
        return record

    async def list_for_student(
        self,
        student_id: uuid.UUID,
        subject: str | None = None,
        grade: str | None = None,
    ) -> list[MasteryRecord]:
        stmt = select(MasteryRecord).where(MasteryRecord.student_id == student_id)
        if subject:
            stmt = stmt.where(MasteryRecord.subject == subject)
        if grade:
            stmt = stmt.where(MasteryRecord.grade == grade)
        result = await self.session.execute(stmt.order_by(MasteryRecord.mastery_prob.asc()))
        return list(result.scalars().all())

    async def get_gaps(
        self,
        student_id: uuid.UUID,
        threshold: float = 0.7,
        subject: str | None = None,
    ) -> list[MasteryRecord]:
        """Return standards below mastery threshold (Postgres view of gaps)."""
        stmt = select(MasteryRecord).where(
            MasteryRecord.student_id == student_id,
            MasteryRecord.mastery_prob < threshold,
        )
        if subject:
            stmt = stmt.where(MasteryRecord.subject == subject)
        result = await self.session.execute(stmt.order_by(MasteryRecord.mastery_prob.asc()))
        return list(result.scalars().all())
