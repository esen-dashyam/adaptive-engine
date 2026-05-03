"""Tests for ProposalStore — in-memory tool-call staging with TTL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.proposal_store import ProposalStore


@pytest.fixture
def store() -> ProposalStore:
    return ProposalStore()


def test_stage_returns_token(store: ProposalStore) -> None:
    t = store.stage(tool="approve_task", args={"x": 1})
    assert isinstance(t, str)
    assert len(t) > 8


def test_pop_returns_call_once(store: ProposalStore) -> None:
    t = store.stage(tool="x", args={"y": 1})
    assert store.pop(t) == ("x", {"y": 1})
    assert store.pop(t) is None


def test_pop_returns_none_when_expired(store: ProposalStore) -> None:
    t = store.stage(tool="x", args={})
    # Force the entry to be expired.
    store._entries[t][2] = datetime.now(timezone.utc) - timedelta(seconds=1)  # type: ignore[index]
    assert store.pop(t) is None


def test_pop_unknown_token_returns_none(store: ProposalStore) -> None:
    assert store.pop("definitely-not-a-token") is None
