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
from backend.app.services.app_catalog import lookup as catalog_lookup
from backend.app.services.bigkid_store import get_store as get_bigkid_store
from backend.app.services.chat_resolver import dispatch, DispatchResult
from backend.app.services.gemini_reflection import generate_reflection_content
from backend.app.schemas.bigkid import QuizQuestionPublic
from backend.app.schemas.agent import Proposal, Receipt

# Force tool modules to import at app startup so @tool decorators
# register their tools into GLOBAL_REGISTRY exactly once. Doing this at
# module top (NOT inside the route function) guarantees the registry is
# populated before the first /parent/chat or /parent/agent/exec request.
# shield_tools is intentionally NOT imported — that path stays on the
# legacy verb-table dispatcher in v1.
from backend.app.services.agent_tools import (  # noqa: F401
    read_tools, task_tools, reflection_tools, bypass_tools,
    lock_tools, vision_tools, shield_tools,
)


def _canonical_display(target_request: str | None) -> str | None:
    """Promote catalog-known aliases to the canonical display name.

    Fixes cards that show the user's raw phrasing ("ig", "tiktok") instead of
    the pretty name ("Instagram", "TikTok"). No-op for anything not in the catalog.
    """
    if not target_request:
        return target_request
    entry = catalog_lookup(target_request)
    return entry.names[0] if entry else target_request


def _card_transition_message(card_id: str, target: str | None, duration_minutes: int | None) -> str:
    """Neutral over-the-card copy. Replaces Gemini's natural-language reply
    when the dispatcher routes to a confirmation card — Gemini doesn't know a
    card will fire, so its "I'll do X" reply would contradict the card.
    See plan Phase 6/9 UX discussion.
    """
    t = target or "that"
    d = f"{duration_minutes} min" if duration_minutes else None

    match card_id:
        # Group A — destructive confirmations
        case "A1":
            return f"Just to confirm — block {t}? This hides it from the home screen until you unblock."
        case "A3":
            return "Unblock everything? Here's what's currently blocked:"
        # Group B — downgrade
        case "B1":
            return f"{t} is currently shielded permanently. Do you want to change it to " + (d or "a timed shield") + "?"
        case "B2":
            return f"{t} is currently blocked. Switch to a timed shield instead?"
        # Group C — upgrade
        case "C1":
            return f"Replace the shield on {t} with a permanent block?"
        case "C2":
            return f"{t} is in a shielded list. Block just this app?"
        # Group D — missing info / ambiguity
        case "D1":
            return f"Before I shield {t} — how long?"
        case "D2":
            return "\"everything\" could mean a few different things. Which one?"
        case "D3":
            return f"That's a long lock ({d or 'over a day'}). Are you sure?"
        case "D4":
            return f"Which child's phone should this apply to?"
        # Group E — rejection + alternative
        case "E1":
            return f"Hmm, I can't shield {t} directly in Standard mode. Here are some alternatives:"
        case "E2":
            return f"Blocking is a Maximum-mode feature. Want to shield {t} temporarily instead?"
        case "E3":
            return f"I don't recognize {t} in my catalog, so I can't hard-block it. But I can try something else:"
        case "E4":
            return f"I don't see a Saved List called \"{t}\". Want to create it?"
        # Group F — fuzzy list match
        case "F1":
            return f"I couldn't find \"{t}\" exactly. Did you mean one of these?"
        # Group G — onboarding
        case "G1":
            return "Maximum mode needs a Child Apple ID on this phone."
        # Group R — reflection confirmation (big-kid mode)
        case "R1":
            return f"Want me to send a reflection about that?"
        case _:
            return f"I need to confirm something before I do that."


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

REFLECTION (big-kid mode) — emit type:"reflect" in TWO situations:

1. EXPLICIT request: "trigger reflection", "make him reflect",
   "give her a reflection", "send X a reflection" → reflect

2. NARRATION OF MISBEHAVIOR: parent describes a child doing something
   wrong (rude language, breaking a rule, hitting a sibling, ignoring a
   chore, lying, screen overuse, tantrum, etc.) WITHOUT explicitly asking
   for a reflection. Examples:
   - "He called me a bitch at dinner"
   - "She hit her sister again"
   - "Liam wouldn't stop scrolling past bedtime"
   - "He lied about his homework"
   → reflect (the host app will surface a confirmation card before firing)

   DO NOT trigger reflect for:
   - Neutral observations ("He had a long day")
   - Positive narration ("She finished her chores!")
   - Questions ("What should I do about screen time?")
   - Vague venting that names no specific bad behavior

When type=="reflect", emit:
{
  "type": "reflect",
  "reflection_reason": "<plain-English description of WHAT the child did wrong (kid-action only, not parent feelings). Avoid 'You did' literal phrasing — the downstream model will rephrase it.>",
  "message": "<empathetic acknowledgement of the parent's frustration in 1–2 sentences. NEVER say 'I'll send a reflection' — the host app handles confirmation.>"
}
Other shield/block fields (target_request, duration_minutes, etc.) are NOT used for reflect.

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
    "type": "shield" | "block" | "unshield" | "unblock" | "unshield_all" | "unblock_all" | "reflect" | null,
    "reflection_reason": "<plain-English reason>" | null,
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

    # Card IDs the parent has already confirmed in-session. Dispatcher uses these
    # to bypass the specific guard and dispatch the underlying action:
    #   "A1" — Max first-time block → bypass A1, queue block Command
    #   "B1" — permanent→timed shield downgrade → set force_downgrade=true
    force_confirmations: list[str] = Field(default_factory=list)

    # Big-kid child id (UUID string). Required for `reflect` action;
    # ignored for everything else. iOS reads this from the same
    # @AppStorage("evlin.childDeviceID") that the BigKid debug panel uses.
    child_device_id: UUID | None = None


class ChatAction(BaseModel):
    type: str
    command_id: UUID | None = None
    tier: str | None = None
    target_display: str | None = None
    duration_minutes: int | None = None
    confirmation_required: bool = False

    # v2 fields
    card_id: str | None = None                      # "A1", "B1", "D1", …
    confirmation_reason: str | None = None
    list_suggestions: list[str] = Field(default_factory=list)
    category_guess: str | None = None


class ChatResponse(BaseModel):
    message: str
    reasoning: str | None = None
    action: ChatAction | None = None
    # Agent-path fields (default empty for back-compat with legacy iOS
    # builds; legacy verb-table path leaves them empty).
    proposals: list[Proposal] = Field(default_factory=list)
    receipts: list[Receipt] = Field(default_factory=list)
    cancelled_proposals: list[str] = Field(default_factory=list)


def _trimmed_snapshot(state, *, child_id: UUID) -> dict:
    """Strip context-bloat from the full ChildStateResponse for the
    agent's auto-injected state. See spec §4.4. Drops quiz body, photo
    URLs, and any large per-task fields.

    `child_id` is included so the agent can pass it to tools that
    require it (e.g. get_kid_state, approve_task) without the parent
    having to type out a UUID."""
    def _human_status(t) -> str:
        # Collapse the (status, phase) pair into a single label the AI
        # cannot misread. Backend's TaskPhase enum has no `done` value,
        # so an approved task surfaces as status=done + phase=submitted —
        # the AI saw `phase=submitted` and replied "awaiting your review"
        # for already-approved tasks. Fix: tell the AI exactly one thing.
        if t.status.value == "done":
            return "approved_done"
        if t.status.value == "submitted":
            return "submitted_awaiting_parent_review"
        if t.status.value == "overdue":
            return "overdue_not_submitted"
        if t.phase.value == "redo":
            return "needs_redo_after_parent_feedback"
        return "not_started"

    return {
        "child_id": str(child_id),
        "child_name": state.child_name,
        "minutes_left": state.minutes_left,
        "minutes_max": state.minutes_max,
        "tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "status_label": _human_status(t),
                "has_photo": bool(t.evidence_photo_url),
                "has_note": bool(t.evidence_note),
                "has_bypass": t.bypass is not None,
                "redo_reason": t.redo_reason,
            }
            for t in state.tasks
        ],
        "reflection_active": state.reflection_request is not None,
        "reflection_reason": (
            state.reflection_request.reason
            if state.reflection_request else None
        ),
        "pending_bypass_count": sum(
            1 for t in state.tasks
            if t.bypass and t.bypass.status.value == "pending"
        ),
    }


@router.post("/chat", response_model=ChatResponse, summary="Parent chat with Evlin AI")
async def parent_chat(
    req: ChatRequest,
    session: AsyncSession = Depends(get_async_session),
) -> ChatResponse:
    """Verb-first dispatcher wiring (plan Phase 6 Task 6.3)."""
    if not settings.gemini_api_key:
        raise HTTPException(503, "Gemini API key not configured")

    # Agent path — feature-flagged. When enabled, route through AgentLoop
    # (function-calling + ToolRegistry) and return the proposals/receipts
    # envelope directly. The legacy verb-table dispatcher below is left
    # untouched for AGENT_ENABLED=0 builds.
    # Agent path. shield/block/lock requests are handled by registered
    # shield_tools (shield_app, unshield_app, block_app, unblock_app)
    # which short-circuit the loop and forward to the legacy dispatcher
    # via a `legacy_gemini_action` payload — see agent_resp handling
    # below. No regex routing needed; the agent decides via tool calls.
    if settings.agent_enabled:
        from backend.app.services.agent_loop import AgentLoop, AgentInput
        from backend.app.services.agent_gemini import GeminiAgentClient
        from backend.app.services.agent_tools import GLOBAL_REGISTRY
        from backend.app.services.parent_action_log import (
            get_log as get_action_log,
        )
        from backend.app.services.proposal_store import get_proposal_store

        state_snapshot: dict | None = None
        if req.child_device_id is not None:
            full = get_bigkid_store().get_state(req.child_device_id)
            state_snapshot = _trimmed_snapshot(full, child_id=req.child_device_id)

        loop = AgentLoop(
            registry=GLOBAL_REGISTRY,
            gemini=GeminiAgentClient(),
            action_log=get_action_log(),
            proposal_store=get_proposal_store(),
        )
        # TEMP: catch all agent-path exceptions and surface them in the
        # response body so we can debug without scraping Railway logs.
        # Remove once the agent path is stable.
        try:
            agent_resp = await loop.run(AgentInput(
                message=req.message,
                history=req.history,
                child_device_id=req.child_device_id,
                child_name=req.child_name,
                state_snapshot=state_snapshot,
                force_confirmations=req.force_confirmations or [],
            ))
        except Exception as exc:
            import traceback
            tb_lines = traceback.format_exception(exc)
            tail = "".join(tb_lines)[-2000:]
            return ChatResponse(
                message=f"[AGENT DEBUG] {type(exc).__name__}: {exc}\n\n--- traceback tail ---\n{tail}",
                reasoning="agent path raised — see message body",
                action=None,
            )
        # Legacy-forward: shield/block/lock tools fired and short-circuited
        # the agent loop with a Gemini-shaped action dict. Run it through
        # the existing dispatch+queue pipeline so iOS gets the same
        # ChatResponse.action shape it would have gotten from the legacy
        # verb-table path (A1/B1/D1-D4 cards or queued Command).
        if agent_resp.legacy_gemini_action is not None:
            return await _handle_gemini_action(
                gemini_action=agent_resp.legacy_gemini_action,
                message=agent_resp.message or "",
                reasoning=agent_resp.reasoning,
                req=req, session=session,
            )
        # Forward proposals + receipts. Reviewer flagged this — earlier
        # drafts dropped them. iOS uses these to render the agent UI.
        return ChatResponse(
            message=agent_resp.message,
            reasoning=agent_resp.reasoning,
            action=None,
            proposals=agent_resp.proposals,
            receipts=agent_resp.receipts,
            cancelled_proposals=agent_resp.cancelled_proposals,
        )

    gemini_action, message, reasoning = await _invoke_gemini(req)
    return await _handle_gemini_action(
        gemini_action=gemini_action, message=message, reasoning=reasoning,
        req=req, session=session,
    )


async def _handle_gemini_action(
    *,
    gemini_action: dict | None,
    message: str,
    reasoning: str | None,
    req: ChatRequest,
    session: AsyncSession,
) -> ChatResponse:
    """Take a Gemini-shaped action dict and execute the legacy verb-table
    flow: load family context, dispatch, branch on DispatchResult, queue
    Command if resolved. Extracted from parent_chat() so the agent path's
    shield_app tool can forward into it without duplicating logic."""
    # No action from Gemini — just a conversational reply
    if gemini_action is None:
        return ChatResponse(message=message, reasoning=reasoning, action=None)

    # Big-kid reflection trigger — intercept BEFORE the shield/block dispatcher.
    # Bypasses the entire family/device/Command pipeline because BigKid lives in
    # its own in-memory store keyed by child_device_id.
    #
    # Confirmation gate (R1 card): the first time the parent describes a
    # misbehavior, we don't fire the reflection — we surface an empathetic
    # message and a confirmation card. Only when the parent re-sends with
    # `force_confirmations=["R1"]` do we actually call Gemini to generate
    # quiz content and write to the BigKidStore.
    if gemini_action.get("type") == "reflect":
        reason = (gemini_action.get("reflection_reason") or "").strip()
        force = req.force_confirmations or []
        if not reason:
            return ChatResponse(
                message="Got it — what did they do? Tell me in one sentence and I'll send the reflection.",
                reasoning=reasoning, action=None,
            )
        if req.child_device_id is None:
            return ChatResponse(
                message="I can't send a reflection — no child device is paired to this app yet. Pair the kid's phone first.",
                reasoning=reasoning, action=None,
            )
        if "R1" not in force:
            # Surface the empathetic Gemini message (already contains the
            # acknowledgement of the parent's frustration) and attach the
            # R1 card. The card body is built on iOS from `target_display`.
            return ChatResponse(
                message=message,
                reasoning=reasoning,
                action=ChatAction(
                    type="reflect",
                    target_display=reason,
                    confirmation_required=True,
                    card_id="R1",
                ),
            )
        # Confirmed — generate content + write to BigKid store.
        try:
            content = await generate_reflection_content(reason=reason)
        except Exception as exc:
            logger.warning("Gemini reflection content generation failed: {}", exc)
            return ChatResponse(
                message=f"I couldn't generate the reflection content right now ({exc!s}). Try again in a moment.",
                reasoning=reasoning, action=None,
            )
        store = get_bigkid_store()
        store.trigger_reflection_with_content(
            req.child_device_id, reason=reason,
            display_reason=content.display_reason,
            video_id=content.video_id, video_title=content.video_title,
            writing_prompt=content.writing_prompt,
            quiz_public=[QuizQuestionPublic(q=q.q, options=q.options) for q in content.quiz],
            correct_indices=[q.correct_index for q in content.quiz],
        )
        confirm = (
            f"Done — reflection sent to {req.child_name}'s phone.\n\n"
            f"They'll see: “{content.display_reason}”\n\n"
            f"Three steps (video → quiz → writing) will unlock their device when finished."
        )
        return ChatResponse(message=confirm, reasoning=reasoning, action=None)

    # Fetch family state for dispatcher
    saved_list_names: list[str] = []
    child_devices: list[Device] = []
    protection_mode = "std"
    if req.family_id is not None:
        list_stmt = select(SavedListMeta.name).where(SavedListMeta.family_id == req.family_id)
        saved_list_names = [row[0] for row in (await session.execute(list_stmt)).all()]

        child_stmt = select(Device).where(
            Device.family_id == req.family_id, Device.mode == DeviceMode.child,
        )
        child_devices = list((await session.execute(child_stmt)).scalars().all())

        # Protection mode lives on Family; fall back to std if missing.
        try:
            from backend.app.db.models.family import Family, ProtectionMode  # local import
            family_row = await session.get(Family, req.family_id)
            if family_row is not None:
                pm = family_row.protection_mode
                protection_mode = pm.value if hasattr(pm, "value") else str(pm)
        except Exception:
            protection_mode = "std"

    result: DispatchResult = dispatch(
        family_id=req.family_id or UUID(int=0),
        protection_mode=protection_mode,
        child_count=len(child_devices),
        saved_list_names=saved_list_names,
        gemini_action=gemini_action,
        force_confirmations=req.force_confirmations or [],
    )

    # Card-returning outcomes — override Gemini's "I'll do X" message with a
    # neutral over-the-card transition. See _card_transition_message.
    if result.requires_card is not None:
        action_type = gemini_action.get("type") or "shield"
        display = _canonical_display(gemini_action.get("target_request"))
        duration = gemini_action.get("duration_minutes") if isinstance(gemini_action.get("duration_minutes"), int) else None
        transition = _card_transition_message(result.requires_card, display, duration)
        return ChatResponse(
            message=transition,
            reasoning=reasoning,
            action=ChatAction(
                type=action_type,
                confirmation_required=True,
                card_id=result.requires_card,
                list_suggestions=result.list_suggestions,
                category_guess=result.category_guess,
                target_display=display,
                duration_minutes=duration,
            ),
        )

    # Gemini-flagged ambiguity (not a specific card)
    if result.confirmation_required:
        return ChatResponse(
            message=message,
            reasoning=reasoning,
            action=ChatAction(
                type=gemini_action.get("type") or "shield",
                confirmation_required=True,
                confirmation_reason=result.confirmation_reason,
            ),
        )

    if result.receipt_only_text:
        return ChatResponse(message=result.receipt_only_text, reasoning=None, action=None)

    # Resolved — queue a Command
    if result.resolved is None:
        return ChatResponse(message=message, reasoning=reasoning, action=None)

    if req.family_id is None:
        logger.info("No family_id in chat request — returning parsed action without queueing")
        return ChatResponse(
            message=message, reasoning=reasoning,
            action=ChatAction(
                type=result.resolved.action,
                tier=result.resolved.tier,
                target_display=result.resolved.target_display,
                duration_minutes=result.resolved.duration_minutes,
            ),
        )

    if not child_devices:
        raise HTTPException(400, "no child device paired to this family")

    # Pick the child device.
    #
    # Single-child families: always use that child. `child_name_hint` is advisory
    # in this case — Gemini may fill it from the ChatRequest.child_name context
    # even when the parent didn't say the name, and the device label often
    # doesn't match ("Fred's iPhone" vs hint "Liam"). Falling back to D4 there
    # would be a false positive. D4 only makes sense for multi-child families.
    #
    # Multi-child families: require a hint, strict-ish match against Device.label
    # (equality or substring, case-insensitive). 0 or >1 matches → D4 re-prompt.
    target_child: Device | None = None
    if len(child_devices) == 1:
        target_child = child_devices[0]
    else:
        hint = result.resolved.child_name_hint
        if hint:
            needle = hint.strip().lower()
            matches = [
                dev for dev in child_devices
                if needle == (dev.label or "").strip().lower()
                or needle in (dev.label or "").strip().lower()
            ]
            if len(matches) == 1:
                target_child = matches[0]
        if target_child is None:
            # Multi-child + (no hint OR hint ambiguous) → ask the parent.
            # Same message-override logic as the dispatcher-side card path:
            # don't let Gemini's "I'll shield X" reply contradict the D4 card.
            display = _canonical_display(result.resolved.target_display)
            transition = _card_transition_message("D4", display, result.resolved.duration_minutes)
            return ChatResponse(
                message=transition,
                reasoning=reasoning,
                action=ChatAction(
                    type=result.resolved.action,
                    confirmation_required=True,
                    card_id="D4",
                    target_display=display,
                    duration_minutes=result.resolved.duration_minutes,
                ),
            )

    # Resolve list_id for savedList-tier actions. Dispatcher only has names; the
    # child executor requires the UUID for recordKey stability (spec §3.2) and
    # will throw ExecuteError.malformed if list_id is missing. We look up the
    # SavedListMeta row by (family_id, name) here.
    resolved_list_id: str | None = result.resolved.list_id
    if (
        resolved_list_id is None
        and result.resolved.tier == "savedList"
        and result.resolved.list_name is not None
        and req.family_id is not None
    ):
        list_row_stmt = select(SavedListMeta.id).where(
            SavedListMeta.family_id == req.family_id,
            SavedListMeta.name == result.resolved.list_name,
        )
        list_row = (await session.execute(list_row_stmt)).scalar_one_or_none()
        if list_row is not None:
            resolved_list_id = str(list_row)
        else:
            # Backend metadata row missing — surface clearly instead of silently
            # queueing a command the child will reject.
            logger.warning(
                "Saved list '{}' not found for family {} — returning E4",
                result.resolved.list_name, req.family_id,
            )
            return ChatResponse(
                message=_card_transition_message("E4", result.resolved.list_name, None),
                reasoning=reasoning,
                action=ChatAction(
                    type=result.resolved.action,
                    confirmation_required=True,
                    card_id="E4",
                    target_display=result.resolved.list_name,
                ),
            )

    payload_target: dict[str, Any] = {
        "bundle_id": result.resolved.bundle_id,
        "list_name": result.resolved.list_name,
        "list_id": resolved_list_id,
        "category_hint": result.resolved.category_hint,
        "target_all": result.resolved.target_all,
        "target_child_id": str(target_child.id),
        "target_display": result.resolved.target_display,
        "original_request": gemini_action.get("target_request", ""),
        "has_pending_blob": False,
        "force_downgrade": result.resolved.force_downgrade,
    }

    payload = {
        "action": result.resolved.action,
        "tier": result.resolved.tier,
        "target": payload_target,
        "duration_minutes": result.resolved.duration_minutes,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }

    cmd = Command(
        family_id=req.family_id,
        target_device_id=target_child.id,
        payload=payload,
        ack_status=AckStatus.pending,
    )
    session.add(cmd)
    await session.flush()
    logger.info("Queued {} command {} for child {}", result.resolved.action, cmd.id, target_child.id)

    return ChatResponse(
        message=message,
        reasoning=reasoning,
        action=ChatAction(
            type=result.resolved.action,
            command_id=cmd.id,
            tier=result.resolved.tier,
            target_display=result.resolved.target_display,
            duration_minutes=result.resolved.duration_minutes,
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

class AckPendingConfirmation(BaseModel):
    card_id: str
    context: dict


class AckStatusResponse(BaseModel):
    command_id: UUID
    status: str
    detail: dict | None = None

    # v2 structured fields (plan Phase 6 Task 6.4). Present when status=="confirmed".
    verb: str | None = None
    displayName: str | None = None
    category: str | None = None
    origRequest: str | None = None
    effectiveState: dict | None = None
    pendingConfirmation: AckPendingConfirmation | None = None


@router.get("/ack-status", response_model=AckStatusResponse, summary="Parent polls for child's ack")
async def get_ack_status(
    command_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> AckStatusResponse:
    cmd = await session.get(Command, command_id)
    if not cmd:
        raise HTTPException(404, "command not found")

    # Build v2 payload when the child posted a rich ack.
    pending = None
    if cmd.ack_card_id is not None:
        pending = AckPendingConfirmation(card_id=cmd.ack_card_id, context=cmd.ack_context or {})

    detail = cmd.ack_detail or {}
    return AckStatusResponse(
        command_id=cmd.id,
        status=cmd.ack_status.value,
        detail=cmd.ack_detail,
        verb=cmd.ack_verb,
        displayName=detail.get("display_name") if isinstance(detail, dict) else None,
        category=detail.get("category") if isinstance(detail, dict) else None,
        origRequest=detail.get("orig_request") if isinstance(detail, dict) else None,
        effectiveState=cmd.ack_effective_state,
        pendingConfirmation=pending,
    )


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
