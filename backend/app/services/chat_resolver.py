"""Resolve parsed parent intent to a concrete lock tier.

Input: target_request (the exact words the parent used) + optional hints from Gemini.
Output: ResolverResult with tier + necessary fields for building a Command payload.

Resolution priority:
  1. Saved List exact name match
  2. Saved List fuzzy match when the parent likely meant a list
  3. Explicit category request
  4. App Catalog lookup (exact alias) -> confirmation, not category fallback
  5. Total miss → confirmation_required

Important product rule:
  Chat defaults to Screen Time shield mode. A bundle ID lock hides the app icon,
  so catalog hits are not used for default app locks. Also, an explicit app name
  must not silently expand to a whole category. Exact app shield requires a
  Saved List / FamilyActivityPicker token; otherwise the parent must confirm a
  broader category lock or create a list first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from backend.app.services.app_catalog import lookup as catalog_lookup


@dataclass
class ResolverResult:
    tier: str | None = None  # "saved_list" | "category" | None
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


def _exact_match_list(target: str, names: list[str]) -> str | None:
    """Case-insensitive exact saved-list match."""
    t = target.strip().lower()
    for name in names:
        if name.lower() == t:
            return name
    return None


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

    # 1. Exact saved-list match always wins. This lets a parent create a list
    # named "微信" and later say "lock 微信" without the catalog hijacking it.
    matched_list = _exact_match_list(target, saved_list_names)
    if matched_list:
        return ResolverResult(tier="saved_list", list_name=matched_list)

    # 2. Fuzzy saved-list match only when the language likely means a list.
    # Do not fuzzy-match arbitrary app names; that can create surprising locks.
    if target_kind_hint in ("list", None):
        matched_list = _fuzzy_match_list(target, saved_list_names)
        if matched_list:
            return ResolverResult(tier="saved_list", list_name=matched_list)

    # 3. Direct category only when the parent explicitly asked for a category.
    if target_kind_hint == "category":
        hint = (category_hint_from_ai or target).lower()
        return ResolverResult(tier="category", category_hint=hint)

    # 4. App catalog (exact alias match)
    #
    # A catalog hit proves we understand the app name, but it still does not give
    # us a FamilyActivityPicker token for shield.applications. Do not silently
    # expand "lock 微信" to "lock all social apps"; require confirmation or a
    # saved list first.
    if target_kind_hint in ("app", None):
        entry = catalog_lookup(target)
        if entry is not None:
            return ResolverResult(
                target_display=entry.names[0],
                category_hint=entry.category_hint,
                confirmation_required=True,
                suggestions=[
                    f"create a saved list for {entry.names[0]}",
                    f"lock the {entry.category_hint} category instead",
                ],
            )

    # 5. AI-inferred category for unknown app names is only a suggestion. The
    # parent has to confirm before we lock an entire category.
    if category_hint_from_ai:
        return ResolverResult(
            target_display=target,
            category_hint=category_hint_from_ai.lower(),
            confirmation_required=True,
            suggestions=[f"lock the {category_hint_from_ai.lower()} category"],
        )

    # 6. Total miss — ask for clarification
    return ResolverResult(
        confirmation_required=True,
        suggestions=saved_list_names[:3] if saved_list_names else ["games", "social", "entertainment"],
    )
