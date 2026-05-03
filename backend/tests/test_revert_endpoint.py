"""Tests for POST /parent/actions/{action_id}/revert (Phase A)."""
from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import bigkid_store as bigkid_store_module
from backend.app.services import parent_action_log as action_log_module
from backend.app.services.parent_action_log import get_log


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    action_log_module._singleton = None  # noqa: SLF001
    bigkid_store_module._singleton = None  # noqa: SLF001
    yield
    action_log_module._singleton = None  # noqa: SLF001
    bigkid_store_module._singleton = None  # noqa: SLF001


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_revert_returns_410_when_action_id_missing(client: TestClient) -> None:
    r = client.post("/api/v1/parent/actions/nonexistent/revert")
    assert r.status_code == 410


def test_revert_executes_inverse_for_approve_task(client: TestClient) -> None:
    """Simulate the agent having approved a task: write a log entry by
    hand, then call revert. Without the agent loop yet, we mark the task
    as 'done' first and let revert flip it via request_redo."""
    cid_str = "11111111-1111-1111-1111-111111111111"
    state = client.get(f"/api/v1/parent/state/{cid_str}").json()
    task_id_str = state["tasks"][0]["id"]
    cid = UUID(cid_str)
    task_id = UUID(task_id_str)

    # Approve the task directly via the (existing, unchanged) endpoint.
    client.post(f"/api/v1/parent/task/{task_id_str}/review", json={"decision": "approve"})

    # Manually record what the agent would have logged.
    log = get_log()
    aid = log.record(
        action_type="approve_task",
        args={"child_id": cid_str, "task_id": task_id_str},
        inverse_action="request_redo",
        inverse_args={"child_id": cid_str, "task_id": task_id_str, "redo_reason": "Reverted"},
        source="agent",
    )

    r = client.post(f"/api/v1/parent/actions/{aid}/revert")
    assert r.status_code == 200

    after = client.get(f"/api/v1/parent/state/{cid_str}").json()
    target = next(t for t in after["tasks"] if t["id"] == task_id_str)
    assert target["status"] == "todo"
    assert target["phase"] == "redo"


def test_double_revert_returns_410(client: TestClient) -> None:
    log = get_log()
    aid = log.record(
        action_type="cancel_reflection",
        args={"child_id": "33333333-3333-3333-3333-333333333333"},
        inverse_action=None, inverse_args={}, source="agent",
    )
    first = client.post(f"/api/v1/parent/actions/{aid}/revert")
    assert first.status_code == 200
    second = client.post(f"/api/v1/parent/actions/{aid}/revert")
    assert second.status_code == 410
