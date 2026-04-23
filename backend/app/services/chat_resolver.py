"""Resolve parsed parent intent to a concrete lock tier.

Input: target_request (the exact words the parent used) + optional hints from Gemini.
Output: ResolverResult with tier + necessary fields for building a Command payload.

Resolution priority:
  1. Saved List name match (fuzzy)
  2. App Catalog lookup (exact alias)
  3. AI-inferred category
  4. Total miss → confirmation_required
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from backend.app.services.app_catalog import lookup as catalog_lookup


@dataclass
class ResolverResult:
    tier: str | None = None  # "exact_bundle" | "saved_list" | "category" | None
    bundle_id: str | None = None
    list_name: str | None = None
    category_hint: str | None = None
    target_display: str | None = None
    confirmation_required: bool = False
    suggestions: list[str] = field(default_factory=list)


def _levenshtein(a: str, b: str) -> int:
    """Standard iterative Levenshtein distance."""
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


def _fuzzy_match_list(target: str, names: list[str], max_distance: int = 2) -> str | None:
    """Find the closest saved-list name within edit distance. None if no close match."""
    t = target.strip().lower()
    best: tuple[str, int] | None = None
    for name in names:
        dist = _levenshtein(t, name.lower())
        if dist <= max_distance and (best is None or dist < best[1]):
            best = (name, dist)
    return best[0] if best else None


def resolve(
    *,
    family_id: UUID | None,
    target_request: str,
    target_kind_hint: str | None,
    saved_list_names: list[str],
    category_hint_from_ai: str | None = None,
) -> ResolverResult:
    """Resolve a parsed parent command to a concrete lock tier.

    Args:
        family_id: parent family UUID (for logging/tracing only).
        target_request: exact words the parent used ("IG", "list 1", "games", "abcd").
        target_kind_hint: Gemini's classification — "app" | "list" | "category" | None.
        saved_list_names: names of all SavedListMeta rows for this family.
        category_hint_from_ai: Gemini's best guess of category if the target is unknown.

    Returns:
        ResolverResult. Caller inspects tier; if None and confirmation_required=True,
        the parent is asked to clarify.
    """
    target = target_request.strip()
    if not target:
        return ResolverResult(confirmation_required=True, suggestions=saved_list_names[:3])

    # 1. Saved list (prefer list match when hint suggests list, but also try otherwise)
    if target_kind_hint in ("list", None):
        matched_list = _fuzzy_match_list(target, saved_list_names)
        if matched_list:
            return ResolverResult(tier="saved_list", list_name=matched_list)

    # 2. App catalog (exact alias match)
    if target_kind_hint in ("app", None):
        entry = catalog_lookup(target)
        if entry is not None:
            return ResolverResult(
                tier="exact_bundle",
                bundle_id=entry.bundle_id,
                target_display=entry.names[0],
                category_hint=entry.category_hint,
            )

    # 3. Direct category (Gemini said "category")
    if target_kind_hint == "category":
        hint = (category_hint_from_ai or target).lower()
        return ResolverResult(tier="category", category_hint=hint)

    # 4. AI-inferred category fallback for unknown targets
    if category_hint_from_ai:
        return ResolverResult(
            tier="category",
            category_hint=category_hint_from_ai.lower(),
        )

    # 5. Total miss — ask for clarification
    return ResolverResult(
        confirmation_required=True,
        suggestions=saved_list_names[:3] if saved_list_names else ["games", "social", "entertainment"],
    )
