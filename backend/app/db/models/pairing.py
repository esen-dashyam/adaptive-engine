"""PairingCode — 6-digit code used once to link a child device to a family."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.family import ProtectionMode


def _default_pairing_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=10)


class PairingCode(Base):
    __tablename__ = "evlin_pairing_codes"

    code: Mapped[str] = mapped_column(String(6), primary_key=True)
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evlin_families.id")
    )
    # Baked in at code generation — child device picks it up via /family/pair
    protection_mode: Mapped[ProtectionMode] = mapped_column(
        SAEnum(ProtectionMode, name="evlin_pairing_protection_mode"),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_default_pairing_expiry
    )
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
