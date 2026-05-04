"""Tests for the agent's shield_tools layer."""
from __future__ import annotations

import pytest

from backend.app.services.agent_tools import GLOBAL_REGISTRY
# Force-load so decorators register before tests run.
from backend.app.services.agent_tools import shield_tools  # noqa: F401


@pytest.mark.asyncio
async def test_shield_app_emits_legacy_action_short_circuit() -> None:
    result = await GLOBAL_REGISTRY.call("shield_app", {
        "target": "his phone", "minutes": 15,
    })
    assert "legacy_gemini_action" in result.public
    action = result.public["legacy_gemini_action"]
    assert action["type"] == "shield"
    assert action["target_request"] == "his phone"
    assert action["duration_minutes"] == 15


@pytest.mark.asyncio
async def test_shield_app_missing_minutes_marks_missing() -> None:
    """Dispatcher uses 'missing' sentinel to surface the D1 picker card."""
    result = await GLOBAL_REGISTRY.call("shield_app", {"target": "Instagram"})
    action = result.public["legacy_gemini_action"]
    assert action["duration_minutes"] == "missing"


@pytest.mark.asyncio
async def test_unshield_app_target_all() -> None:
    result = await GLOBAL_REGISTRY.call("unshield_app", {"target_all": True})
    action = result.public["legacy_gemini_action"]
    assert action["type"] == "unshield_all"
    assert action["target_kind_hint"] == "all"


@pytest.mark.asyncio
async def test_block_app_with_duration_flags_confirmation() -> None:
    """Block + duration is ambiguous — must signal block_with_duration so
    the dispatcher emits the confirmation card instead of silently
    queueing a permanent block."""
    result = await GLOBAL_REGISTRY.call("block_app", {
        "target": "TikTok", "minutes": 30,
    })
    action = result.public["legacy_gemini_action"]
    assert action["type"] == "block"
    assert action["confirmation_required"] is True
    assert action["confirmation_reason"] == "block_with_duration"
