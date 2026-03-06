"""
Student and per-standard mastery records (PostgreSQL).

Neo4j (:Student)-[:HAS_SKILL]->(:SkillState) is the PRIMARY source of BKT truth.
These tables are the SECONDARY copy for analytics, audit, and reporting queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base


class Student(Base):
    __tablename__ = "students"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    external_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True,
        comment="Application-level student identifier (e.g. 'student_001')"
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    grade_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    overall_ability: Mapped[float] = mapped_column(Float, default=0.3)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    mastery_records: Mapped[list[MasteryRecord]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[AssessmentSession]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )


class MasteryRecord(Base):
    """One row per student × standard_code — the BKT audit trail."""

    __tablename__ = "mastery_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    standard_code: Mapped[str] = mapped_column(
        String(512), nullable=False,
        comment="LC identifier or statementCode e.g. M.3.12"
    )
    subject: Mapped[str | None] = mapped_column(String(100), nullable=True)
    grade: Mapped[str | None] = mapped_column(String(10), nullable=True)

    mastery_prob: Mapped[float] = mapped_column(
        Float, default=0.1,
        comment="BKT P(mastered) ∈ [0,1]"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    last_assessed: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    student: Mapped[Student] = relationship(back_populates="mastery_records")


# avoid circular import
from backend.app.db.models.assessment import AssessmentSession  # noqa: E402, F401
