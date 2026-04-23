"""Family — endpoints for pairing a parent device + child device into an Evlin family.

POST /api/v1/family/create           — Parent creates a family, gets pairing code
POST /api/v1/family/pair             — Child enters code, joins family
GET  /api/v1/family/pairing-status   — Parent polls for child join
GET  /api/v1/family/auth-status      — Parent polls for `.child` auth granted
POST /api/v1/family/auth-status/grant — Child posts after .child auth succeeds
POST /api/v1/family/saved-lists      — Either device saves list metadata
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.engine import get_async_session
from backend.app.db.models.command import Command
from backend.app.db.models.device import Device, DeviceMode
from backend.app.db.models.family import Family, ProtectionMode
from backend.app.db.models.pairing import PairingCode
from backend.app.db.models.saved_list import SavedListMeta, SavedListMode


router = APIRouter(prefix="/family", tags=["Evlin Family"])


# ---------- /family/create ----------

class CreateFamilyRequest(BaseModel):
    child_name: str
    child_age: int | None = None
    protection_mode: ProtectionMode
    parent_device_label: str = "Parent's iPhone"


class CreateFamilyResponse(BaseModel):
    family_id: uuid.UUID
    parent_device_id: uuid.UUID
    pairing_code: str
    code_expires_at: datetime


def _gen_code() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(6))


@router.post("/create", response_model=CreateFamilyResponse, summary="Parent creates a family")
async def create_family(
    req: CreateFamilyRequest,
    session: AsyncSession = Depends(get_async_session),
) -> CreateFamilyResponse:
    family = Family(protection_mode=req.protection_mode)
    session.add(family)
    await session.flush()

    parent_device = Device(
        family_id=family.id,
        mode=DeviceMode.parent,
        label=req.parent_device_label,
    )
    session.add(parent_device)
    await session.flush()

    # Retry on the extremely unlikely collision (1 in 1M)
    for _ in range(5):
        code = _gen_code()
        existing = await session.get(PairingCode, code)
        if existing is None:
            break
    else:
        raise HTTPException(500, "could not generate unique pairing code")

    pairing = PairingCode(code=code, family_id=family.id, protection_mode=req.protection_mode)
    session.add(pairing)
    await session.flush()

    logger.info("Evlin family {} created for child={} mode={}", family.id, req.child_name, req.protection_mode)

    return CreateFamilyResponse(
        family_id=family.id,
        parent_device_id=parent_device.id,
        pairing_code=code,
        code_expires_at=pairing.expires_at,
    )


# ---------- /family/pair ----------

class PairRequest(BaseModel):
    code: str
    device_label: str = "Child's iPhone"


class PairResponse(BaseModel):
    family_id: uuid.UUID
    child_device_id: uuid.UUID
    parent_device_id: uuid.UUID
    protection_mode: ProtectionMode


@router.post("/pair", response_model=PairResponse, summary="Child pairs into a family")
async def pair(req: PairRequest, session: AsyncSession = Depends(get_async_session)) -> PairResponse:
    pairing = await session.get(PairingCode, req.code)
    if not pairing:
        raise HTTPException(404, "pairing code not found")
    if pairing.used:
        raise HTTPException(400, "pairing code already used")

    # Compare both sides as timezone-aware UTC (pairing.expires_at is TZ-aware from model)
    now = datetime.now(timezone.utc)
    if pairing.expires_at < now:
        raise HTTPException(400, "pairing code expired")

    child_device = Device(
        family_id=pairing.family_id,
        mode=DeviceMode.child,
        label=req.device_label,
    )
    session.add(child_device)
    pairing.used = True

    parent_stmt = select(Device).where(
        Device.family_id == pairing.family_id,
        Device.mode == DeviceMode.parent,
    )
    parent_device = (await session.execute(parent_stmt)).scalar_one_or_none()
    if parent_device is None:
        raise HTTPException(500, "parent device missing for this family")

    await session.flush()

    logger.info("Device {} paired into family {}", child_device.id, pairing.family_id)

    return PairResponse(
        family_id=pairing.family_id,
        child_device_id=child_device.id,
        parent_device_id=parent_device.id,
        protection_mode=pairing.protection_mode,
    )


# ---------- /family/pairing-status ----------

class PairingStatusResponse(BaseModel):
    code: str
    used: bool
    child_device_id: uuid.UUID | None


@router.get("/pairing-status", response_model=PairingStatusResponse, summary="Parent polls for child join")
async def pairing_status(code: str, session: AsyncSession = Depends(get_async_session)) -> PairingStatusResponse:
    pairing = await session.get(PairingCode, code)
    if not pairing:
        raise HTTPException(404, "pairing code not found")

    child_id: uuid.UUID | None = None
    if pairing.used:
        stmt = select(Device).where(
            Device.family_id == pairing.family_id,
            Device.mode == DeviceMode.child,
        )
        child = (await session.execute(stmt)).scalar_one_or_none()
        if child:
            child_id = child.id

    return PairingStatusResponse(code=code, used=pairing.used, child_device_id=child_id)


# ---------- /family/auth-status ----------
# Max mode only: after pairing, child requests .child auth (parent approves on their device
# via Family Sharing). Once granted, child posts to /grant. Parent polls the status.

class AuthStatusResponse(BaseModel):
    family_id: uuid.UUID
    granted: bool


@router.get("/auth-status", response_model=AuthStatusResponse, summary="Poll Max-mode .child auth status")
async def auth_status(family_id: uuid.UUID, session: AsyncSession = Depends(get_async_session)) -> AuthStatusResponse:
    stmt = select(Device).where(
        Device.family_id == family_id,
        Device.mode == DeviceMode.child,
    )
    child = (await session.execute(stmt)).scalar_one_or_none()
    return AuthStatusResponse(
        family_id=family_id,
        granted=bool(child and child.child_auth_granted),
    )


class GrantAuthRequest(BaseModel):
    child_device_id: uuid.UUID


@router.post("/auth-status/grant", summary="Child device reports .child auth granted")
async def grant_auth(
    req: GrantAuthRequest,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    child = await session.get(Device, req.child_device_id)
    if not child:
        raise HTTPException(404, "child device not found")
    child.child_auth_granted = True
    return {"ok": True}


# ---------- /family/saved-lists ----------

class CreateSavedListRequest(BaseModel):
    family_id: uuid.UUID
    owning_device_id: uuid.UUID
    name: str
    description: str | None = None
    mode: SavedListMode


class SavedListResponse(BaseModel):
    id: uuid.UUID
    name: str
    mode: SavedListMode


@router.post("/saved-lists", response_model=SavedListResponse, summary="Upsert saved-list metadata")
async def upsert_saved_list(
    req: CreateSavedListRequest,
    session: AsyncSession = Depends(get_async_session),
) -> SavedListResponse:
    stmt = select(SavedListMeta).where(
        SavedListMeta.family_id == req.family_id,
        SavedListMeta.name == req.name,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        existing.description = req.description
        existing.mode = req.mode
        existing.owning_device_id = req.owning_device_id
        return SavedListResponse(id=existing.id, name=existing.name, mode=existing.mode)

    row = SavedListMeta(
        family_id=req.family_id,
        owning_device_id=req.owning_device_id,
        name=req.name,
        description=req.description,
        mode=req.mode,
    )
    session.add(row)
    await session.flush()
    return SavedListResponse(id=row.id, name=row.name, mode=row.mode)


class SavedListSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    mode: SavedListMode


@router.get("/saved-lists", response_model=list[SavedListSummary], summary="List saved-list metadata for a family")
async def list_saved_lists(
    family_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> list[SavedListSummary]:
    stmt = select(SavedListMeta).where(SavedListMeta.family_id == family_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        SavedListSummary(id=r.id, name=r.name, description=r.description, mode=r.mode)
        for r in rows
    ]
