"""Parent Chat — Agentic AI endpoint for parental control commands.

POST /api/v1/parent/chat                  — Parse intent, resolve tier, queue Command
POST /api/v1/parent/commands/attach-blob  — Attach Max-mode selection blob to command
GET  /api/v1/parent/ack-status            — Poll child device ack status
GET  /api/v1/parent/youtube-recommendations — YouTube video recommendations
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.settings import settings
from backend.app.db.engine import get_async_session
from backend.app.db.models.command import Command, AckStatus
from backend.app.db.models.device import Device, DeviceMode
from backend.app.db.models.saved_list import SavedListMeta
from backend.app.services.chat_resolver import resolve


router = APIRouter(prefix="/parent", tags=["Parent Chat"])


SYSTEM_PROMPT = """You are Evlin, an AI-powered parental control assistant. You parse the parent's natural-
language commands into a structured action. You do NOT execute; you interpret.

VERB → INTENT MAPPING — STRICT:

shield / lock / pause / restrict / limit / silence  →  "shield"
block / hide / ban                                   →  "block"
unshield / unlock / release / allow                  →  "unshield"
unblock / restore / bring back                       →  "unblock"
"unlock everything" / "unlock all" / "clear locks"   →  "unshield_all"
"unblock everything" / "unblock all"                 →  "unblock_all"

AMBIGUOUS VERBS — must trigger confirmation_required:
remove / kill / delete / stop / close / end / get rid of

These could mean either "shield the timer" or "block the app" — do NOT guess.
Set confirmation_required: true, confirmation_reason: "ambiguous_verb".

NEVER CROSS-TRANSLATE:
- "lock forever" / "lock permanently" → shield with duration_minutes=null (NOT block)
- "block for 30 min" → confirmation_required: true, reason: "block_with_duration"
  (Block is permanent; ask the parent if they meant shield for 30 min.)

TARGET KIND HINT:
- "list 1" / "bedtime apps" / "homework block"        → kind=list
- "all games" / "social apps" / "entertainment"       → kind=category
- "IG" / "Instagram" / "TikTok" / app name            → kind=app
- "everything" / "all apps" / "his phone" / "all"     → kind=all (EXPLICIT)
- "everything he wastes" / "distracting stuff"        → kind=null (AMBIGUOUS, will trigger D2)

DURATION EXTRACTION:
- "for 30 min" / "for 2 hours" / "for 3 days"         → integer minutes
- "until 8 PM" / "until bedtime"                      → compute minutes from now
- "permanently" / "forever" / "until I unlock"        → duration_minutes: null
- NO duration phrase                                  → duration_minutes: "missing"
                                                        (dispatcher will show D1)

CATEGORY HINT:
Always attempt to provide category_hint_from_ai as a fallback signal, even when kind=app.
Choose from: "social", "games", "entertainment", "productivity", "education".

CHILD NAME DETECTION:
If the parent explicitly names a child ("Liam's", "Emma's"), include child_name_hint.
If family has multiple children and no name is mentioned, leave child_name_hint=null
(dispatcher will show D4 multi-child picker).

RESPONSE FORMAT (always valid JSON):
{
  "message": "conversational reply to the parent",
  "reasoning": "brief internal analysis",
  "action": {
    "type": "shield" | "block" | "unshield" | "unblock" | "unshield_all" | "unblock_all" | null,
    "target_request": "<parent's original target phrase>",
    "target_kind_hint": "app" | "list" | "category" | "all" | null,
    "duration_minutes": <int> | null | "missing",
    "category_hint_from_ai": "social" | "games" | ... | null,
    "child_name_hint": "<name>" | null,
    "confirmation_required": <bool>,
    "confirmation_reason": "ambiguous_verb" | "block_with_duration" | null
  }
}

If the message isn't a command (e.g. "how is Liam doing?"), set action to null.
ALWAYS return valid JSON. No markdown code fences."""


class ChatRequest(BaseModel):
    message: str
    family_id: UUID | None = None  # None allowed for legacy calls (no pairing)
    child_name: str = "Liam"
    history: list[dict[str, str]] = Field(default_factory=list)


class ChatAction(BaseModel):
    type: str
    command_id: UUID | None = None
    tier: str | None = None            # saved_list | category | None (for confirmation)
    target_display: str | None = None
    duration_minutes: int | None = None
    confirmation_required: bool = False


class ChatResponse(BaseModel):
    message: str
    reasoning: str | None = None
    action: ChatAction | None = None


@router.post("/chat", response_model=ChatResponse, summary="Parent chat with Evlin AI")
async def parent_chat(
    req: ChatRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ChatResponse:
    if not settings.gemini_api_key:
        raise HTTPException(503, "Gemini API key not configured")

    gemini_action, message, reasoning = await _invoke_gemini(req)

    # No action from Gemini — just a conversational reply
    if gemini_action is None:
        return ChatResponse(message=message, reasoning=reasoning, action=None)

    action_type = _normalize_global_action_type(
        message_text=req.message,
        action_type=gemini_action.get("type", "lock"),
        target_request=gemini_action.get("target_request", ""),
    )

    # Non-lock actions bypass the resolver
    if action_type in ("lock_all", "unlock_all"):
        target_display = "all apps" if action_type == "lock_all" else "all locks"
        action_out = await _queue_simple_command(
            req=req, session=session, action_type=action_type,
            target_display=target_display, payload_target={"original_request": "all"},
        )
        return ChatResponse(
            message=message, reasoning=reasoning,
            action=ChatAction(
                type=action_type,
                command_id=action_out.command_id if action_out else None,
                target_display=target_display,
            ),
        )

    # Lock / Unlock — run resolver
    saved_list_names: list[str] = []
    if req.family_id is not None:
        stmt = select(SavedListMeta.name).where(SavedListMeta.family_id == req.family_id)
        saved_list_names = [row[0] for row in (await session.execute(stmt)).all()]

    resolved = resolve(
        family_id=req.family_id,
        target_request=gemini_action.get("target_request", ""),
        target_kind_hint=gemini_action.get("target_kind_hint"),
        saved_list_names=saved_list_names,
        category_hint_from_ai=gemini_action.get("category_hint_from_ai"),
    )

    if resolved.confirmation_required:
        confirmation_message = _confirmation_message(
            action_type=action_type,
            target_request=gemini_action.get("target_request", ""),
            resolved=resolved,
        )
        return ChatResponse(
            message=confirmation_message, reasoning=reasoning,
            action=ChatAction(
                type=action_type,
                confirmation_required=True,
                target_display=resolved.target_display or gemini_action.get("target_request"),
            ),
        )

    # No family paired — return the parsed action for dev purposes but don't queue
    if req.family_id is None:
        logger.info("No family_id in chat request — returning parsed action without queueing")
        return ChatResponse(
            message=message, reasoning=reasoning,
            action=ChatAction(
                type=action_type,
                tier=resolved.tier,
                target_display=resolved.target_display or resolved.list_name or resolved.category_hint,
                duration_minutes=gemini_action.get("duration_minutes"),
            ),
        )

    # Look up the child device for this family
    child_stmt = select(Device).where(
        Device.family_id == req.family_id,
        Device.mode == DeviceMode.child,
    )
    child = (await session.execute(child_stmt)).scalar_one_or_none()
    if child is None:
        raise HTTPException(400, "no child device paired to this family")

    payload_target: dict[str, Any] = {
        "bundle_id": resolved.bundle_id,
        "list_name": resolved.list_name,
        "has_pending_blob": False,    # Max mode: set True via /parent/commands/attach-blob
        "category_hint": resolved.category_hint,
        "original_request": gemini_action.get("target_request", ""),
        "target_display": resolved.target_display,
    }

    payload = {
        "action": action_type,
        "tier": resolved.tier,
        "target": payload_target,
        "duration_minutes": gemini_action.get("duration_minutes"),
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }

    cmd = Command(
        family_id=req.family_id,
        target_device_id=child.id,
        payload=payload,
        ack_status=AckStatus.pending,
    )
    session.add(cmd)
    await session.flush()
    logger.info("Queued {} command {} for child {}", action_type, cmd.id, child.id)

    return ChatResponse(
        message=message,
        reasoning=reasoning,
        action=ChatAction(
            type=action_type,
            command_id=cmd.id,
            tier=resolved.tier,
            target_display=resolved.target_display or resolved.list_name or resolved.category_hint,
            duration_minutes=gemini_action.get("duration_minutes"),
        ),
    )


# ----- attach-blob endpoint (Max mode ephemeral relay) -----

class AttachBlobRequest(BaseModel):
    command_id: UUID
    selection_blob_b64: str


@router.post("/commands/attach-blob", summary="Parent attaches Max-mode selection blob to a queued command")
async def attach_blob(
    req: AttachBlobRequest,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    import base64
    from backend.app.db.models.command import PendingBlob

    cmd = await session.get(Command, req.command_id)
    if not cmd:
        raise HTTPException(404, "command not found")

    try:
        blob_bytes = base64.b64decode(req.selection_blob_b64)
    except Exception as exc:
        raise HTTPException(400, f"invalid base64 blob: {exc}")

    session.add(PendingBlob(command_id=req.command_id, blob=blob_bytes))

    # Flip target.has_pending_blob in the payload
    payload = dict(cmd.payload or {})
    target = dict(payload.get("target", {}))
    target["has_pending_blob"] = True
    payload["target"] = target
    cmd.payload = payload

    return {"ok": True, "command_id": str(req.command_id)}


# ----- /parent/ack-status -----

class AckStatusResponse(BaseModel):
    command_id: UUID
    status: str
    detail: dict | None


@router.get("/ack-status", response_model=AckStatusResponse, summary="Parent polls for child's ack")
async def get_ack_status(
    command_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> AckStatusResponse:
    cmd = await session.get(Command, command_id)
    if not cmd:
        raise HTTPException(404, "command not found")
    return AckStatusResponse(command_id=cmd.id, status=cmd.ack_status.value, detail=cmd.ack_detail)


# ----- Gemini wrapper + simple queue helper -----

def _normalize_global_action_type(*, message_text: str, action_type: str, target_request: str) -> str:
    """Coerce obvious whole-device intents even if Gemini emits plain lock/unlock."""
    combined = f"{message_text} {target_request}".lower()
    global_markers = (
        "lock all",
        "lock everything",
        "lock the whole phone",
        "lock whole phone",
        "ban all apps",
        "all apps",
        "everything",
        "整个手机",
        "所有 app",
        "全部 app",
        "全锁",
    )
    unlock_markers = (
        "unlock all",
        "unlock everything",
        "clear all locks",
        "all locks",
        "全部解锁",
        "全部解除",
    )
    if action_type == "lock" and any(marker in combined for marker in global_markers):
        return "lock_all"
    if action_type == "unlock" and any(marker in combined for marker in unlock_markers):
        return "unlock_all"
    return action_type

def _confirmation_message(
    *,
    action_type: str,
    target_request: str,
    resolved,
) -> str:
    """Human-safe confirmation copy when resolver refuses to broaden a lock."""
    target = resolved.target_display or target_request or "that app"
    category = resolved.category_hint
    if action_type == "unlock":
        return f"I need a more precise target before unlocking {target}."
    if category:
        return (
            f"I know {target} is usually a {category} app, but I won't lock the entire "
            f"{category} category unless you explicitly ask for that. Create or select a "
            f"Saved List for {target}, or say \"lock {category} apps\" if you want the whole category blocked."
        )
    return (
        f"I need a specific Saved List or category before locking {target}. "
        "For a shield-style single-app lock, first add that app to a Saved List with the picker."
    )

async def _invoke_gemini(req: ChatRequest) -> tuple[dict | None, str, str | None]:
    """Returns (action_dict_or_None, message, reasoning)."""
    from google import genai
    from google.genai import types

    history_text = ""
    for h in req.history[-10:]:
        role = "Parent" if h.get("role") == "parent" else "Evlin"
        history_text += f"{role}: {h.get('content', '')}\n"

    user_msg = f"{history_text}Parent: [Child: {req.child_name}] {req.message}"
    full_prompt = f"{SYSTEM_PROMPT}\n\n---\nConversation:\n{user_msg}\n\nRespond as Evlin in JSON:"

    client = genai.Client(api_key=settings.gemini_api_key)
    resp = client.models.generate_content(
        model=settings.gemini_model,
        contents=full_prompt,
        config=types.GenerateContentConfig(
            temperature=0.7,
            response_mime_type="application/json",
        ),
    )
    text = (resp.text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    data = json.loads(text)
    return data.get("action"), data.get("message", ""), data.get("reasoning")


class _QueuedCommand(BaseModel):
    command_id: UUID


async def _queue_simple_command(
    *, req: ChatRequest, session: AsyncSession,
    action_type: str, target_display: str, payload_target: dict,
) -> _QueuedCommand | None:
    """Queue commands that do not need the resolver. Returns None if no family paired."""
    if req.family_id is None:
        return None
    child_stmt = select(Device).where(
        Device.family_id == req.family_id,
        Device.mode == DeviceMode.child,
    )
    child = (await session.execute(child_stmt)).scalar_one_or_none()
    if child is None:
        return None

    payload = {
        "action": action_type,
        "tier": None,
        "target": payload_target,
        "duration_minutes": None,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    cmd = Command(family_id=req.family_id, target_device_id=child.id, payload=payload)
    session.add(cmd)
    await session.flush()
    return _QueuedCommand(command_id=cmd.id)


# ----- Preserved: YouTube recommendations -----

@router.get("/youtube-recommendations", summary="Get YouTube video recommendations")
async def youtube_recommendations(
    query: str = Query(default="parenting screen time management children"),
    max_results: int = Query(default=5, le=10),
) -> list[dict]:
    """Search YouTube for parenting/screen-time related videos."""
    try:
        from backend.app.services.youtube_search import search_edu_videos

        results = search_edu_videos(
            topic=query,
            top_n=max_results,
        )

        return [
            {
                "video_id": v.get("video_id", ""),
                "title": v.get("title", ""),
                "channel": v.get("channel", ""),
                "thumbnail": v.get("thumbnail", ""),
            }
            for v in results
        ]
    except Exception as e:
        logger.error("YouTube search failed: {}", e)
        raise HTTPException(status_code=500, detail=str(e))
