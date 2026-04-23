"""Tests for the chat resolver — covers each tier + confirmation fallback."""
from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.services.chat_resolver import resolve


@pytest.fixture
def fid():
    return uuid4()


# ----- Tier A: catalog hit -----

def test_catalog_hit_IG(fid):
    r = resolve(
        family_id=fid, target_request="IG",
        target_kind_hint=None, saved_list_names=[],
    )
    assert r.tier == "exact_bundle"
    assert r.bundle_id == "com.burbn.instagram"
    assert r.target_display == "Instagram"
    assert r.category_hint == "social"


def test_catalog_hit_with_app_hint(fid):
    r = resolve(
        family_id=fid, target_request="TikTok",
        target_kind_hint="app", saved_list_names=[],
    )
    assert r.tier == "exact_bundle"
    assert r.bundle_id == "com.zhiliaoapp.musically"


def test_catalog_hit_chinese_alias(fid):
    r = resolve(
        family_id=fid, target_request="抖音",
        target_kind_hint=None, saved_list_names=[],
    )
    assert r.tier == "exact_bundle"
    assert r.bundle_id == "com.zhiliaoapp.musically"


# ----- Tier B: saved list -----

def test_saved_list_exact(fid):
    r = resolve(
        family_id=fid, target_request="list 1",
        target_kind_hint=None, saved_list_names=["list 1", "bedtime apps"],
    )
    assert r.tier == "saved_list"
    assert r.list_name == "list 1"


def test_saved_list_fuzzy(fid):
    # edit distance 1: "bedtime app" vs "bedtime apps"
    r = resolve(
        family_id=fid, target_request="bedtime app",
        target_kind_hint=None, saved_list_names=["bedtime apps", "list 1"],
    )
    assert r.tier == "saved_list"
    assert r.list_name == "bedtime apps"


def test_saved_list_beats_catalog_when_hint_says_list(fid):
    # If the parent literally has a saved list called "instagram", and
    # says "lock instagram" with hint=list, the list wins over catalog.
    r = resolve(
        family_id=fid, target_request="instagram",
        target_kind_hint="list", saved_list_names=["instagram"],
    )
    assert r.tier == "saved_list"
    assert r.list_name == "instagram"


def test_saved_list_fuzzy_too_far_misses(fid):
    # Distance 3+ should NOT match — fallback to catalog/category
    r = resolve(
        family_id=fid, target_request="bedroom apps",  # distance to "bedtime apps" = 3
        target_kind_hint=None, saved_list_names=["bedtime apps"],
    )
    # Shouldn't match list — may fall to confirmation or category
    assert r.tier != "saved_list"


# ----- Tier C: category -----

def test_category_direct(fid):
    r = resolve(
        family_id=fid, target_request="games",
        target_kind_hint="category", saved_list_names=[],
    )
    assert r.tier == "category"
    assert r.category_hint == "games"


def test_category_inferred_from_ai(fid):
    r = resolve(
        family_id=fid, target_request="abcd",
        target_kind_hint=None, saved_list_names=[],
        category_hint_from_ai="games",
    )
    assert r.tier == "category"
    assert r.category_hint == "games"


def test_category_hint_lowercased(fid):
    r = resolve(
        family_id=fid, target_request="all Games",
        target_kind_hint="category", saved_list_names=[],
        category_hint_from_ai="Games",
    )
    assert r.tier == "category"
    assert r.category_hint == "games"


# ----- Miss / confirmation -----

def test_total_miss_requires_confirmation(fid):
    r = resolve(
        family_id=fid, target_request="totallyunknown",
        target_kind_hint=None, saved_list_names=[],
    )
    assert r.confirmation_required is True
    assert len(r.suggestions) > 0


def test_empty_target_requires_confirmation(fid):
    r = resolve(
        family_id=fid, target_request="",
        target_kind_hint=None, saved_list_names=["list 1"],
    )
    assert r.confirmation_required is True
    assert "list 1" in r.suggestions


# ----- Priority ordering -----

def test_list_hint_does_not_pick_catalog(fid):
    # hint=list but the target matches a catalog alias — should NOT return exact_bundle
    # since the user's hint says it's a list
    r = resolve(
        family_id=fid, target_request="IG",
        target_kind_hint="list", saved_list_names=[],
    )
    # No list matches, no category hint — should end up confirmation_required
    assert r.confirmation_required is True


def test_app_hint_skips_list_check(fid):
    # hint=app + target matches a saved list exactly — hint wins, go to catalog
    r = resolve(
        family_id=fid, target_request="IG",
        target_kind_hint="app", saved_list_names=["IG"],  # unlikely but test it
    )
    # With hint=app, skip step 1 (list), go straight to catalog which hits
    assert r.tier == "exact_bundle"
    assert r.bundle_id == "com.burbn.instagram"
