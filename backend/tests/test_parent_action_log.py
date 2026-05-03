"""Tests for the ParentActionLog service (Phase A)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# Inline singleton-reset fixture (Phase A). Phase B.0 extracts this into
# backend/tests/conftest.py under a different name; both can coexist.
@pytest.fixture(autouse=True)
def _reset_singletons_action_log() -> None:
    from backend.app.services import parent_action_log as action_log_module

    action_log_module._singleton = None  # noqa: SLF001
    yield
    action_log_module._singleton = None  # noqa: SLF001


from backend.app.services.parent_action_log import ParentActionLog


@pytest.fixture
def log() -> ParentActionLog:
    return ParentActionLog()


def test_record_returns_unique_action_id(log: ParentActionLog) -> None:
    a = log.record(
        action_type="approve_task", args={"task_id": "T1"},
        inverse_action="request_redo", inverse_args={"task_id": "T1", "reason": "Reverted"},
        source="profile_ui",
    )
    b = log.record(
        action_type="approve_task", args={"task_id": "T2"},
        inverse_action="request_redo", inverse_args={"task_id": "T2", "reason": "Reverted"},
        source="profile_ui",
    )
    assert a != b
    assert log.get(a).action_type == "approve_task"
    assert log.get(a).args == {"task_id": "T1"}


def test_get_returns_none_when_action_missing(log: ParentActionLog) -> None:
    assert log.get("does-not-exist") is None


def test_record_sets_default_60s_ttl(log: ParentActionLog) -> None:
    aid = log.record(
        action_type="x", args={}, inverse_action="y", inverse_args={}, source="agent",
    )
    entry = log.get(aid)
    delta = entry.expires_at - entry.created_at
    assert 55 <= delta.total_seconds() <= 65


def test_get_returns_none_for_expired_entry(log: ParentActionLog) -> None:
    aid = log.record(
        action_type="x", args={}, inverse_action="y", inverse_args={}, source="agent",
    )
    # Force expiry
    entry = log._entries[aid]  # noqa: SLF001
    entry.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert log.get(aid) is None


def test_mark_reverted_prevents_double_revert(log: ParentActionLog) -> None:
    aid = log.record(
        action_type="x", args={}, inverse_action="y", inverse_args={}, source="agent",
    )
    log.mark_reverted(aid)
    assert log.get(aid).reverted is True
    # second revert via mark_reverted is idempotent — does not raise
    log.mark_reverted(aid)
