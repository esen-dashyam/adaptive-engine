"""Verb-first dispatcher for parental-control chat commands.

Entry point: `dispatch(family_id, protection_mode, child_count, saved_list_names,
                       gemini_action, force_confirmations=None)`.

Returns a DispatchResult telling the /parent/chat handler whether to:
  - Create and queue a Command row (resolved path), or
  - Return a card ID to the client (confirmation path), or
  - Emit a receipt-only text response (short-circuit path).

See spec §6.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from backend.app.services.app_catalog import lookup as catalog_lookup


@dataclass
class ResolvedAction:
    """The concrete action to queue when no card is needed.

    `category_hint` is required (when available) for unshield/unblock paths —
    iOS uses it to detect category coverage of the target app, so receipts
    report accurate effective state after mutation.
    """
    action: str            # "shield" | "block" | "unshield" | "unblock" | "unshield_all" | "unblock_all"
    tier: str | None       # "exactApp" | "savedList" | "category" | "all"
    bundle_id: str | None = None
    list_id: str | None = None
    list_name: str | None = None
    category_hint: str | None = None
    target_all: bool = False
    target_display: str | None = None
    duration_minutes: int | None = None
    # Set true when the parent's B1 card confirmation re-submitted the Chat message.
    force_downgrade: bool = False
    # Which child the command targets. Forwarded from gemini_action.child_name_hint.
    child_name_hint: str | None = None


@dataclass
class DispatchResult:
    """What the /parent/chat endpoint should do with this command."""
    resolved: ResolvedAction | None = None
    requires_card: str | None = None
    receipt_only_text: str | None = None

    # Side-channel info carried to the client for card rendering.
    confirmation_required: bool = False
    confirmation_reason: str | None = None
    list_suggestions: list[str] = field(default_factory=list)
    category_guess: str | None = None


# ---------- Public entry point ----------

def dispatch(
    *,
    family_id: UUID,
    protection_mode: str,            # "std" | "max"
    child_count: int,
    saved_list_names: list[str],
    gemini_action: dict,
    force_confirmations: list[str] | None = None,
) -> DispatchResult:
    """Route a Gemini-parsed action to a concrete dispatch outcome."""
    action_type = gemini_action.get("type")
    if action_type is None:
        return DispatchResult(receipt_only_text="(conversational reply — no action)")

    # Short-circuit: Gemini-flagged ambiguity
    if gemini_action.get("confirmation_required"):
        return DispatchResult(
            confirmation_required=True,
            confirmation_reason=gemini_action.get("confirmation_reason"),
        )

    # D4 multi-child check — runs BEFORE verb routing (spec §6 step 2)
    if child_count >= 2 and not gemini_action.get("child_name_hint"):
        return DispatchResult(requires_card="D4")

    # Route by verb
    if action_type == "block":
        result = _route_block(protection_mode, gemini_action, force_confirmations)
    elif action_type == "unblock":
        result = _route_unblock(protection_mode, gemini_action)
    elif action_type == "unblock_all":
        result = _route_unblock_all(protection_mode, force_confirmations)
    elif action_type == "shield":
        result = _route_shield(protection_mode, saved_list_names, gemini_action, force_confirmations)
    elif action_type == "unshield":
        result = _route_unshield(protection_mode, gemini_action)
    elif action_type == "unshield_all":
        result = DispatchResult(resolved=ResolvedAction(action="unshield_all", tier=None))
    else:
        return DispatchResult(receipt_only_text=f"Unknown action: {action_type}")

    # Thread child_name_hint from Gemini onto any queued ResolvedAction so the
    # /parent/chat handler can pick the right Device. Single-child families
    # still match without a hint (handler falls back to the only child).
    if result.resolved is not None:
        result.resolved.child_name_hint = gemini_action.get("child_name_hint")
    return result


# ---------- Verb handlers ----------

def _route_block(
    mode: str,
    action: dict,
    force_confirmations: list[str] | None = None,
) -> DispatchResult:
    if mode == "std":
        return DispatchResult(requires_card="E2")

    target = action.get("target_request", "")
    entry = catalog_lookup(target)
    if entry is None:
        return DispatchResult(
            requires_card="E3",
            category_guess=action.get("category_hint_from_ai"),
        )

    resolved = ResolvedAction(
        action="block",
        tier=None,
        bundle_id=entry.bundle_id,
        target_display=entry.names[0],
    )

    # Parent already confirmed A1 in the previous turn → dispatch the block directly.
    if force_confirmations and "A1" in force_confirmations:
        return DispatchResult(resolved=resolved)

    # First-time block → A1 confirm card. Primary re-sends /parent/chat with force_confirmations=["A1"].
    return DispatchResult(requires_card="A1", resolved=resolved)


def _route_unblock(mode: str, action: dict) -> DispatchResult:
    # Spec D5: unblock works in BOTH modes. Direct action — no card (A2 removed).
    entry = catalog_lookup(action.get("target_request", ""))
    category_hint = (
        entry.category_hint if entry else None
    ) or action.get("category_hint_from_ai")
    return DispatchResult(
        resolved=ResolvedAction(
            action="unblock",
            tier=None,
            bundle_id=entry.bundle_id if entry else None,
            target_display=(entry.names[0] if entry else action.get("target_request")),
            category_hint=category_hint,
        )
    )


def _route_unblock_all(mode: str, force_confirmations: list[str] | None = None) -> DispatchResult:
    # Spec D5: unblock_all works in BOTH modes but always requires confirmation.
    # Parent's A3-card Confirm re-submits with force_confirmations=["A3"], which
    # bypasses the guard and queues the actual unblock_all command.
    if force_confirmations and "A3" in force_confirmations:
        return DispatchResult(resolved=ResolvedAction(action="unblock_all", tier=None))
    return DispatchResult(requires_card="A3")


def _route_shield(
    mode: str,
    saved_list_names: list[str],
    action: dict,
    force_confirmations: list[str] | None = None,
) -> DispatchResult:
    force_downgrade = bool(force_confirmations and "B1" in force_confirmations)
    skip_long_duration_guard = bool(force_confirmations and "D3" in force_confirmations)
    kind = action.get("target_kind_hint")
    target = action.get("target_request", "")
    duration = action.get("duration_minutes")

    # D1: missing duration
    if duration == "missing":
        return DispatchResult(requires_card="D1")

    # D3: long duration (>24h). Parent's D3 Confirm re-submits with
    # force_confirmations=["D3"] to bypass and queue the actual shield.
    if isinstance(duration, int) and duration > 24 * 60 and not skip_long_duration_guard:
        return DispatchResult(requires_card="D3")

    # D2: ambiguous "everything"
    if kind is None:
        target_lower = target.lower()
        ambiguous_markers = [
            "everything he wastes",
            "everything liam wastes",
            "stuff",
            "distracting",
            "distractions",
        ]
        if any(m in target_lower for m in ambiguous_markers):
            return DispatchResult(requires_card="D2")

    if kind == "all":
        return DispatchResult(
            resolved=ResolvedAction(
                action="shield",
                tier="all",
                target_all=True,
                target_display="All Apps",
                duration_minutes=duration if isinstance(duration, int) else None,
                force_downgrade=force_downgrade,
            )
        )

    if kind == "category":
        return DispatchResult(
            resolved=ResolvedAction(
                action="shield",
                tier="category",
                category_hint=action.get("category_hint_from_ai", target.lower()),
                target_display=target,
                duration_minutes=duration if isinstance(duration, int) else None,
                force_downgrade=force_downgrade,
            )
        )

    if kind == "list":
        matched = _exact_list_match(target, saved_list_names)
        if matched:
            return DispatchResult(
                resolved=ResolvedAction(
                    action="shield",
                    tier="savedList",
                    list_name=matched,
                    target_display=matched,
                    duration_minutes=duration if isinstance(duration, int) else None,
                    force_downgrade=force_downgrade,
                )
            )
        suggestions = _fuzzy_list_matches(target, saved_list_names, max_distance=2)
        if suggestions:
            return DispatchResult(requires_card="F1", list_suggestions=suggestions)
        return DispatchResult(requires_card="E4")

    if kind == "app":
        # Std can't shield single apps; Max remote picker not in MVP → both offer E1 fallback.
        return DispatchResult(
            requires_card="E1",
            category_guess=action.get("category_hint_from_ai"),
        )

    # Fallthrough — treat as ambiguous
    return DispatchResult(requires_card="D2")


def _route_unshield(mode: str, action: dict) -> DispatchResult:
    kind = action.get("target_kind_hint")
    if kind == "list":
        return DispatchResult(
            resolved=ResolvedAction(
                action="unshield",
                tier="savedList",
                list_name=action.get("target_request"),
            )
        )
    if kind == "category":
        return DispatchResult(
            resolved=ResolvedAction(
                action="unshield",
                tier="category",
                category_hint=action.get("target_request", "").lower(),
            )
        )
    if kind == "all":
        return DispatchResult(
            resolved=ResolvedAction(
                action="unshield",
                tier="all",
                target_all=True,
            )
        )
    # Default: app-level unshield
    entry = catalog_lookup(action.get("target_request", ""))
    category_hint = (
        entry.category_hint if entry else None
    ) or action.get("category_hint_from_ai")
    return DispatchResult(
        resolved=ResolvedAction(
            action="unshield",
            tier="exactApp",
            bundle_id=entry.bundle_id if entry else None,
            target_display=(entry.names[0] if entry else action.get("target_request")),
            category_hint=category_hint,
        )
    )


# ---------- Fuzzy / exact list matching ----------

def _exact_list_match(target: str, names: list[str]) -> str | None:
    """Case-insensitive + whitespace/hyphen-normalized exact match."""
    normalized_target = _normalize(target)
    for name in names:
        if _normalize(name) == normalized_target:
            return name
    return None


def _normalize(s: str) -> str:
    return s.strip().lower().replace(" ", "").replace("-", "")


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    dp = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(dp[j] + 1, row[-1] + 1, dp[j - 1] + (ca != cb)))
        dp = row
    return dp[-1]


def _fuzzy_list_matches(target: str, names: list[str], max_distance: int) -> list[str]:
    target_lower = target.strip().lower()
    candidates: list[tuple[str, int]] = []
    for name in names:
        d = _levenshtein(target_lower, name.lower())
        if d <= max_distance and d > 0:
            candidates.append((name, d))
    candidates.sort(key=lambda t: t[1])
    return [name for name, _ in candidates]


# ---------- Legacy shim for parent_chat.py (v1 `resolve` call path) ----------
# Kept because some tests / older callers import it. Maps onto the new dispatcher.

@dataclass
class ResolverResult:
    tier: str | None = None
    list_name: str | None = None
    list_id: str | None = None
    category_hint: str | None = None
    bundle_id: str | None = None
    target_display: str | None = None
    confirmation_required: bool = False
    confirmation_reason: str | None = None
    list_suggestions: list[str] = field(default_factory=list)
    category_guess: str | None = None


def resolve(*args, **kwargs) -> ResolverResult:  # pragma: no cover - legacy shim
    """Legacy entry — returns an empty result. Callers should migrate to dispatch()."""
    return ResolverResult(confirmation_required=True, confirmation_reason="legacy_caller_use_dispatch")
