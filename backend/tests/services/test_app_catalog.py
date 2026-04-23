"""Tests for the static app catalog."""
from __future__ import annotations

from backend.app.services.app_catalog import load_catalog, lookup, primary_name


def test_lookup_instagram_by_alias():
    entry = lookup("IG")
    assert entry is not None
    assert entry.bundle_id == "com.burbn.instagram"
    assert entry.category_hint == "social"


def test_lookup_canonical_name_hits():
    assert lookup("Instagram").bundle_id == "com.burbn.instagram"
    assert lookup("insta").bundle_id == "com.burbn.instagram"


def test_lookup_case_insensitive():
    assert lookup("tiktok") is not None
    assert lookup("TIKTOK") is not None
    assert lookup("TikTok").bundle_id == "com.zhiliaoapp.musically"


def test_lookup_chinese_aliases():
    assert lookup("抖音").bundle_id == "com.zhiliaoapp.musically"
    assert lookup("微信").bundle_id == "com.tencent.xin"
    assert lookup("原神").bundle_id == "com.miHoYo.GenshinImpact"


def test_lookup_miss():
    assert lookup("totallyunknownapp") is None
    assert lookup("") is None


def test_catalog_size_sanity():
    assert len(load_catalog()) >= 50


def test_all_entries_have_nonempty_fields():
    for entry in load_catalog():
        assert len(entry.names) >= 1, f"empty names: {entry}"
        assert entry.bundle_id, f"empty bundle_id: {entry}"
        assert entry.category_hint, f"empty category_hint: {entry}"


def test_primary_name_helper():
    entry = lookup("IG")
    assert primary_name(entry) == "Instagram"  # canonical
