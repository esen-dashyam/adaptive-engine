"""Tests for verb-first dispatch logic (spec §6)."""
from __future__ import annotations

import pytest
from uuid import uuid4

from backend.app.services.chat_resolver import dispatch


def _gemini_action(
    type_: str,
    target: str = "IG",
    kind: str | None = None,
    duration: int | str | None = None,
    category_hint: str | None = None,
    confirmation_required: bool = False,
    confirmation_reason: str | None = None,
    child_name_hint: str | None = None,
) -> dict:
    return {
        "type": type_,
        "target_request": target,
        "target_kind_hint": kind,
        "duration_minutes": duration,
        "category_hint_from_ai": category_hint,
        "child_name_hint": child_name_hint,
        "confirmation_required": confirmation_required,
        "confirmation_reason": confirmation_reason,
    }


def test_block_in_std_mode_routes_to_e2():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="std",
        child_count=1,
        saved_list_names=[],
        gemini_action=_gemini_action("block", "IG"),
    )
    assert result.requires_card == "E2"


def test_block_catalog_hit_in_max_routes_to_a1():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="max",
        child_count=1,
        saved_list_names=[],
        gemini_action=_gemini_action("block", "IG", kind="app"),
    )
    assert result.requires_card == "A1"
    assert result.resolved.bundle_id == "com.burbn.instagram"


def test_block_catalog_miss_in_max_routes_to_e3():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="max",
        child_count=1,
        saved_list_names=[],
        gemini_action=_gemini_action("block", "abcd", kind="app"),
    )
    assert result.requires_card == "E3"


def test_shield_missing_duration_routes_to_d1():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="std",
        child_count=1,
        saved_list_names=["list 1"],
        gemini_action=_gemini_action("shield", "list 1", kind="list", duration="missing"),
    )
    assert result.requires_card == "D1"


def test_multi_child_no_name_routes_to_d4():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="std",
        child_count=3,
        saved_list_names=[],
        gemini_action=_gemini_action("shield", "list 1", kind="list", duration=30),
    )
    assert result.requires_card == "D4"


def test_ambiguous_verb_routes_to_clarification():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="std",
        child_count=1,
        saved_list_names=[],
        gemini_action=_gemini_action(
            "block", "IG",
            confirmation_required=True,
            confirmation_reason="ambiguous_verb",
        ),
    )
    assert result.confirmation_required
    assert result.confirmation_reason == "ambiguous_verb"


def test_unblock_works_in_std_as_direct_action():
    """Std can unblock (for leftover blocks from Max downgrades) — spec D5.
    Single-item unblock is a DIRECT action — no card (spec §5.2 A2 removed)."""
    result = dispatch(
        family_id=uuid4(),
        protection_mode="std",
        child_count=1,
        saved_list_names=[],
        gemini_action=_gemini_action("unblock", "IG", kind="app"),
    )
    assert result.requires_card is None
    assert result.resolved is not None
    assert result.resolved.action == "unblock"
    assert result.resolved.bundle_id == "com.burbn.instagram"


def test_shield_single_app_in_std_routes_to_e1():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="std",
        child_count=1,
        saved_list_names=[],
        gemini_action=_gemini_action(
            "shield", "Instagram", kind="app", duration=30, category_hint="social",
        ),
    )
    assert result.requires_card == "E1"


def test_ambiguous_everything_routes_to_d2():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="std",
        child_count=1,
        saved_list_names=[],
        gemini_action=_gemini_action("shield", "everything he wastes", kind=None, duration=30),
    )
    assert result.requires_card == "D2"


def test_fuzzy_list_match_routes_to_f1():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="std",
        child_count=1,
        saved_list_names=["list 1", "bedtime apps"],
        gemini_action=_gemini_action("shield", "list 11", kind="list", duration=30),
    )
    assert result.requires_card == "F1"
    assert result.list_suggestions == ["list 1"]


def test_no_list_match_no_close_routes_to_e4():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="std",
        child_count=1,
        saved_list_names=["list 1", "bedtime apps"],
        gemini_action=_gemini_action("shield", "totally unknown list", kind="list", duration=30),
    )
    assert result.requires_card == "E4"


def test_a1_force_confirmation_bypasses_a1_card_and_resolves_block():
    # First turn: parent says "block IG" → A1 card (Max mode, catalog hits).
    initial = dispatch(
        family_id=uuid4(),
        protection_mode="max",
        child_count=1,
        saved_list_names=[],
        gemini_action=_gemini_action("block", "Instagram", kind="app"),
    )
    assert initial.requires_card == "A1"
    assert initial.resolved is not None  # still carried so card can display target

    # Second turn: parent tapped "Block Instagram" on A1 → re-send with force.
    confirmed = dispatch(
        family_id=uuid4(),
        protection_mode="max",
        child_count=1,
        saved_list_names=[],
        gemini_action=_gemini_action("block", "Instagram", kind="app"),
        force_confirmations=["A1"],
    )
    assert confirmed.requires_card is None
    assert confirmed.resolved is not None
    assert confirmed.resolved.action == "block"


def test_child_name_hint_threaded_onto_resolved_action():
    result = dispatch(
        family_id=uuid4(),
        protection_mode="std",
        child_count=1,
        saved_list_names=["list 1"],
        gemini_action=_gemini_action(
            "shield", "list 1", kind="list", duration=30, child_name_hint="Liam"
        ),
    )
    assert result.resolved is not None
    assert result.resolved.child_name_hint == "Liam"
