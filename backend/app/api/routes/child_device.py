"""Child Device — endpoints for child phone to register and poll for actions.

POST /api/v1/child/register — Register a child device
GET  /api/v1/child/pending-actions/{child_id} — Poll for pending actions
POST /api/v1/child/ack/{action_id} — Acknowledge action was executed
POST /api/v1/parent/device-action — Parent sends an action to child's device
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(tags=["Child Device"])

# In-memory store (replace with Supabase table later)
_registered_children: dict[str, dict] = {}
_pending_actions: list[dict] = []


# ── Models ──

class ChildRegisterRequest(BaseModel):
    child_id: str
    child_name: str
    device_name: str = "iPhone"


class DeviceActionRequest(BaseModel):
    child_id: str
    action_type: str  # lock, unlock, block_category
    params: dict[str, Any] = Field(default_factory=dict)  # e.g. {"minutes": 30, "categories": ["games"]}


# ── Child endpoints ──

@router.post("/child/register", summary="Register a child device")
async def register_child(req: ChildRegisterRequest) -> dict:
    _registered_children[req.child_id] = {
        "child_id": req.child_id,
        "child_name": req.child_name,
        "device_name": req.device_name,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("Child device registered: {} ({})", req.child_name, req.child_id)
    return {"status": "registered", "child_id": req.child_id}


@router.get("/child/pending-actions/{child_id}", summary="Poll for pending actions")
async def get_pending_actions(child_id: str) -> list[dict]:
    # Update last_seen
    if child_id in _registered_children:
        _registered_children[child_id]["last_seen"] = datetime.now(timezone.utc).isoformat()

    # Return unacknowledged actions for this child
    pending = [
        a for a in _pending_actions
        if a["child_id"] == child_id and not a.get("acknowledged", False)
    ]
    return pending


@router.post("/child/ack/{action_id}", summary="Acknowledge action was executed")
async def acknowledge_action(action_id: str) -> dict:
    for a in _pending_actions:
        if a["action_id"] == action_id:
            a["acknowledged"] = True
            a["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("Action acknowledged: {}", action_id)
            return {"status": "acknowledged"}
    raise HTTPException(status_code=404, detail="Action not found")


# ── Parent endpoint ──

@router.post("/parent/device-action", summary="Send action to child device")
async def send_device_action(req: DeviceActionRequest) -> dict:
    action_id = str(uuid4())
    action = {
        "action_id": action_id,
        "child_id": req.child_id,
        "action_type": req.action_type,
        "params": req.params,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "acknowledged": False,
    }
    _pending_actions.append(action)
    logger.info("Action queued for {}: {} ({})", req.child_id, req.action_type, action_id)

    # Cleanup old acknowledged actions (keep last 50)
    global _pending_actions
    _pending_actions = [a for a in _pending_actions if not a.get("acknowledged")] + \
                       [a for a in _pending_actions if a.get("acknowledged")][-10:]

    return {"status": "queued", "action_id": action_id}


# ── Status endpoint ──

@router.get("/parent/children", summary="List registered child devices")
async def list_children() -> list[dict]:
    return list(_registered_children.values())
