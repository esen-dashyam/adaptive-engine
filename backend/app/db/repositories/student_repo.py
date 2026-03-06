"""Student repository — Postgres CRUD for Student rows."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.student import Student


class StudentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_external_id(self, external_id: str) -> Student | None:
        result = await self.session.execute(
            select(Student).where(Student.external_id == external_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self, external_id: str, display_name: str | None = None, grade: str | None = None
    ) -> Student:
        student = await self.get_by_external_id(external_id)
        if student is None:
            student = Student(
                external_id=external_id,
                display_name=display_name,
                grade_level=grade,
            )
            self.session.add(student)
            await self.session.flush()
        return student
