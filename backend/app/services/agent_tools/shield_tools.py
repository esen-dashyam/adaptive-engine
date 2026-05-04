"""Shield/block/lock tools — bridge to the legacy verb-table dispatcher.

Why: agent gets one global registry, but shield/block/lock requests need
the existing chat_resolver.dispatch() pipeline (A1/B1/D1-D4 cards +
Command queue + ack-status polling). Rather than rewriting that logic,
these tools build a Gemini-shaped action dict and signal AgentLoop to
exit the loop — parent_chat then forwards the dict through
_handle_gemini_action, reusing the entire existing flow unchanged.

The tools are deliberately simple: they don't talk to the DB or call
the dispatcher directly. The agent picks parameters; this layer just
formats them into the legacy contract.
"""
from __future__ import annotations

from typing import Optional

from backend.app.services.agent_tools.decorator import (
    GLOBAL_REGISTRY, ToolResult, tool,
)


@tool(
    name="shield_app",
    description=(
        "Shield (silence/lock/restrict/pause) an app or category on the kid's "
        "device for a duration. Use this for ANY shield-shaped request — "
        "'lock his phone for 30 min', 'shield Instagram', 'block tiktok' "
        "(treat 'block X for Y' as shield since block is permanent), "
        "'silence games', 'restrict social media', 'pause his phone'. "
        "After this tool runs, the parent will see the existing confirmation "
        "card flow (D1 to pick duration, A1 to confirm a block, etc.) — DO NOT "
        "re-confirm with the parent in your message; just call the tool. "
        "If duration is missing, leave minutes=null and the dispatcher will "
        "show a quick-pick card."
    ),
    requires_confirm=False,
    danger="medium",
    registry=GLOBAL_REGISTRY,
)
async def shield_app(
    target: str,
    minutes: Optional[int] = None,
    target_kind: str = "app",
    category_hint: Optional[str] = None,
) -> ToolResult:
    """Build a `shield`-typed gemini_action dict and short-circuit the loop.
    target_kind is "app" | "list" | "category" | "all" matching the legacy
    SYSTEM_PROMPT contract. category_hint helps the dispatcher offer
    smart fallbacks (E1 card) when an exact target can't be resolved."""
    legacy_action = {
        "type": "shield",
        "target_request": target,
        "target_kind_hint": target_kind,
        "duration_minutes": minutes if minutes is not None else "missing",
        "category_hint_from_ai": category_hint,
        "child_name_hint": None,
        "confirmation_required": False,
        "confirmation_reason": None,
    }
    return ToolResult(
        public={"legacy_gemini_action": legacy_action},
        public_summary=(
            f"Forwarding shield request: {target}"
            + (f" for {minutes} min" if minutes else "")
        ),
    )


@tool(
    name="unshield_app",
    description=(
        "Remove a shield from an app or all apps. Use for 'unlock his phone', "
        "'unshield Instagram', 'release the apps', 'remove the lock', "
        "'unlock everything', 'clear all locks'. If the parent says 'unlock "
        "everything' / 'clear all', set target_all=True."
    ),
    requires_confirm=False,
    danger="low",
    registry=GLOBAL_REGISTRY,
)
async def unshield_app(
    target: Optional[str] = None,
    target_all: bool = False,
) -> ToolResult:
    """Build an unshield gemini_action. If target_all, type becomes
    unshield_all and target_kind=all (dispatcher handles bulk)."""
    if target_all:
        legacy_action = {
            "type": "unshield_all",
            "target_request": target or "everything",
            "target_kind_hint": "all",
            "duration_minutes": None,
            "category_hint_from_ai": None,
            "child_name_hint": None,
            "confirmation_required": False,
            "confirmation_reason": None,
        }
        summary = "Forwarding unshield-all request"
    else:
        legacy_action = {
            "type": "unshield",
            "target_request": target or "",
            "target_kind_hint": "app",
            "duration_minutes": None,
            "category_hint_from_ai": None,
            "child_name_hint": None,
            "confirmation_required": False,
            "confirmation_reason": None,
        }
        summary = f"Forwarding unshield request: {target}"
    return ToolResult(
        public={"legacy_gemini_action": legacy_action},
        public_summary=summary,
    )


@tool(
    name="block_app",
    description=(
        "Block (permanently hide) an app from the kid's home screen. Use for "
        "'block Instagram', 'hide TikTok', 'ban the games', 'remove this app'. "
        "Block is PERMANENT — if the parent says 'block X for 30 min' that's "
        "ambiguous (block doesn't expire); the dispatcher will show a "
        "confirmation card asking if they meant shield-for-30."
    ),
    requires_confirm=False,
    danger="high",
    registry=GLOBAL_REGISTRY,
)
async def block_app(
    target: str,
    minutes: Optional[int] = None,
    target_kind: str = "app",
) -> ToolResult:
    legacy_action = {
        "type": "block",
        "target_request": target,
        "target_kind_hint": target_kind,
        "duration_minutes": minutes,  # if set + non-null, dispatcher emits "block_with_duration" confirm
        "category_hint_from_ai": None,
        "child_name_hint": None,
        "confirmation_required": minutes is not None,
        "confirmation_reason": "block_with_duration" if minutes is not None else None,
    }
    return ToolResult(
        public={"legacy_gemini_action": legacy_action},
        public_summary=f"Forwarding block request: {target}",
    )


@tool(
    name="unblock_app",
    description=(
        "Unblock an app or all apps. Use for 'unblock Instagram', 'restore "
        "TikTok', 'unblock everything', 'bring back all apps'."
    ),
    requires_confirm=False,
    danger="low",
    registry=GLOBAL_REGISTRY,
)
async def unblock_app(
    target: Optional[str] = None,
    target_all: bool = False,
) -> ToolResult:
    if target_all:
        legacy_action = {
            "type": "unblock_all",
            "target_request": target or "everything",
            "target_kind_hint": "all",
            "duration_minutes": None,
            "category_hint_from_ai": None,
            "child_name_hint": None,
            "confirmation_required": False,
            "confirmation_reason": None,
        }
        summary = "Forwarding unblock-all request"
    else:
        legacy_action = {
            "type": "unblock",
            "target_request": target or "",
            "target_kind_hint": "app",
            "duration_minutes": None,
            "category_hint_from_ai": None,
            "child_name_hint": None,
            "confirmation_required": False,
            "confirmation_reason": None,
        }
        summary = f"Forwarding unblock request: {target}"
    return ToolResult(
        public={"legacy_gemini_action": legacy_action},
        public_summary=summary,
    )
