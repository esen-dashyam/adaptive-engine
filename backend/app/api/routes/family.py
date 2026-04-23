"""Family — endpoints for pairing a parent device + child device into an Evlin family.

POST /api/v1/family/create           — Child creates a family, gets pairing code to show
POST /api/v1/family/pair             — Parent enters code, joins family
GET  /api/v1/family/pairing-status   — Child polls for parent join
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
# Child initiates family: creates family + child device + pairing code.

class CreateFamilyRequest(BaseModel):
    child_device_label: str = "Child's iPhone"
    protection_mode: ProtectionMode = ProtectionMode.std


class CreateFamilyResponse(BaseModel):
    family_id: uuid.UUID
    child_device_id: uuid.UUID
    pairing_code: str
    code_expires_at: datetime


def _gen_code() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(6))


@router.post("/create", response_model=CreateFamilyResponse, summary="Child initiates family + gets pairing code")
async def create_family(
    req: CreateFamilyRequest,
    session: AsyncSession = Depends(get_async_session),
) -> CreateFamilyResponse:
    family = Family(protection_mode=req.protection_mode)
    session.add(family)
    await session.flush()

    child_device = Device(
        family_id=family.id,
        mode=DeviceMode.child,
        label=req.child_device_label,
    )
    session.add(child_device)
    await session.flush()

    # Retry on the extremely unlikely collision
    code = ""
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

    logger.info("Evlin family {} created by child device {} mode={}", family.id, child_device.id, req.protection_mode)

    return CreateFamilyResponse(
        family_id=family.id,
        child_device_id=child_device.id,
        pairing_code=code,
        code_expires_at=pairing.expires_at,
    )


# ---------- /family/pair ----------
# Parent joins an existing family by entering the code the child showed.

class PairRequest(BaseModel):
    code: str
    parent_device_label: str = "Parent's iPhone"


class PairResponse(BaseModel):
    family_id: uuid.UUID
    parent_device_id: uuid.UUID
    child_device_id: uuid.UUID
    protection_mode: ProtectionMode


@router.post("/pair", response_model=PairResponse, summary="Parent enters code to join family")
async def pair(req: PairRequest, session: AsyncSession = Depends(get_async_session)) -> PairResponse:
    pairing = await session.get(PairingCode, req.code)
    if not pairing:
        raise HTTPException(404, "pairing code not found")
    if pairing.used:
        raise HTTPException(400, "pairing code already used")

    now = datetime.now(timezone.utc)
    if pairing.expires_at < now:
        raise HTTPException(400, "pairing code expired")

    parent_device = Device(
        family_id=pairing.family_id,
        mode=DeviceMode.parent,
        label=req.parent_device_label,
    )
    session.add(parent_device)
    pairing.used = True

    child_stmt = select(Device).where(
        Device.family_id == pairing.family_id,
        Device.mode == DeviceMode.child,
    )
    child_device = (await session.execute(child_stmt)).scalar_one_or_none()
    if child_device is None:
        raise HTTPException(500, "child device missing for this family")

    await session.flush()

    logger.info("Parent device {} paired into family {}", parent_device.id, pairing.family_id)

    return PairResponse(
        family_id=pairing.family_id,
        parent_device_id=parent_device.id,
        child_device_id=child_device.id,
        protection_mode=pairing.protection_mode,
    )


# ---------- /family/pairing-status ----------
# Child polls this while displaying the code to know when parent has joined.

class PairingStatusResponse(BaseModel):
    code: str
    used: bool
    parent_device_id: uuid.UUID | None


@router.get("/pairing-status", response_model=PairingStatusResponse, summary="Child polls for parent join")
async def pairing_status(code: str, session: AsyncSession = Depends(get_async_session)) -> PairingStatusResponse:
    pairing = await session.get(PairingCode, code)
    if not pairing:
        raise HTTPException(404, "pairing code not found")

    parent_id: uuid.UUID | None = None
    if pairing.used:
        stmt = select(Device).where(
            Device.family_id == pairing.family_id,
            Device.mode == DeviceMode.parent,
        )
        parent = (await session.execute(stmt)).scalar_one_or_none()
        if parent:
            parent_id = parent.id

    return PairingStatusResponse(code=code, used=pairing.used, parent_device_id=parent_id)


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
