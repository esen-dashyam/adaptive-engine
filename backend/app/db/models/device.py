"""Device — a parent or child phone paired into an Evlin family."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class DeviceMode(str, enum.Enum):
    parent = "parent"
    child = "child"


class Device(Base):
    __tablename__ = "evlin_devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evlin_families.id"),
        index=True,
    )
    mode: Mapped[DeviceMode] = mapped_column(
        SAEnum(DeviceMode, name="evlin_device_mode")
    )
    label: Mapped[str] = mapped_column(String(120))
    apns_token: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Max-mode only: true once child has granted `.child` auth via Family Sharing
    child_auth_granted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
