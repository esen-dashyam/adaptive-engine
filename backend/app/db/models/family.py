"""Family — top-level container for Evlin parental control pairings."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class ProtectionMode(str, enum.Enum):
    max = "max"
    std = "std"


class Family(Base):
    __tablename__ = "evlin_families"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    protection_mode: Mapped[ProtectionMode] = mapped_column(
        SAEnum(ProtectionMode, name="evlin_protection_mode"),
        default=ProtectionMode.std,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
