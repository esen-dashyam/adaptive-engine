"""End-to-end tests for big-kid /child and /parent endpoints."""
from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

CHILD = "11111111-1111-1111-1111-111111111111"
HEADERS = {"X-Child-Id": CHILD}


@pytest.fixture
def client() -> TestClient:
    # Reset the in-memory store between tests.
    from backend.app.services import bigkid_store
    bigkid_store._singleton = None
    return TestClient(app)


def test_get_state_returns_seeded_response(client: TestClient) -> None:
    r = client.get("/api/v1/child/state", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["child_name"] == "Liam"
    assert body["minutes_left"] == 0
    assert body["minutes_max"] == 120
    assert len(body["tasks"]) == 3
    assert body["reflection_request"] is None


def test_post_evidence_marks_task_submitted(client: TestClient) -> None:
    state = client.get("/api/v1/child/state", headers=HEADERS).json()
    task_id = state["tasks"][0]["id"]
    files = {"photo": ("evidence.jpg", b"\xff\xd8\xff\xe0fakejpg", "image/jpeg")}
    r = client.post(
        f"/api/v1/child/task/{task_id}/evidence",
        headers=HEADERS, files=files, data={"note": "All done!"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "submitted"
    assert body["phase"] == "submitted"
    assert body["evidence_photo_url"].startswith("https://")


def test_bypass_then_evidence_withdraws_bypass(client: TestClient) -> None:
    state = client.get("/api/v1/child/state", headers=HEADERS).json()
    task_id = state["tasks"][0]["id"]
    # Submit bypass
    r = client.post(
        "/api/v1/child/bypass", headers=HEADERS,
        json={"task_id": task_id, "reason": "I had a fever"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    # Submit evidence on same task → bypass auto-withdraws
    files = {"photo": ("e.jpg", b"\xff\xd8\xff", "image/jpeg")}
    client.post(f"/api/v1/child/task/{task_id}/evidence", headers=HEADERS, files=files)
    after = client.get("/api/v1/child/state", headers=HEADERS).json()
    after_task = next(t for t in after["tasks"] if t["id"] == task_id)
    assert after_task["bypass"]["status"] == "withdrawn"


def test_reflection_full_flow(client: TestClient) -> None:
    # Parent triggers
    r = client.post(
        "/api/v1/parent/reflection/trigger",
        json={"child_id": CHILD, "reason": "stayed up too late"},
    )
    assert r.status_code == 200
    rid = r.json()["id"]

    # Child sees it
    state = client.get("/api/v1/child/state", headers=HEADERS).json()
    assert state["reflection_request"]["id"] == rid
    assert state["reflection_request"]["status"] == "pending"

    # Video step
    r = client.post(
        f"/api/v1/child/reflection/{rid}/step-complete",
        headers=HEADERS, json={"step": "video"},
    )
    assert r.status_code == 200

    # Quiz: answer 5 questions correctly
    fixture_correct = [0, 1, 2, 1, 1]  # from seed
    body = None
    for i, correct_idx in enumerate(fixture_correct):
        r = client.post(
            f"/api/v1/child/reflection/{rid}/quiz-answer",
            headers=HEADERS,
            json={"question_index": i, "selected_index": correct_idx},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["correct"] is True
    assert body is not None
    assert body["all_correct"] is True
    assert body["score"] == 5

    # Essay
    r = client.post(
        f"/api/v1/child/reflection/{rid}/essay",
        headers=HEADERS,
        json={"text": "I felt tired and grumpy. Next time I will go to bed when asked. I'm sorry."},
    )
    assert r.status_code == 200
    after = client.get("/api/v1/child/state", headers=HEADERS).json()
    assert after["reflection_request"]["status"] == "submitted"

    # Nudge starts cooldown
    r = client.post(f"/api/v1/child/reflection/{rid}/nudge", headers=HEADERS)
    assert r.status_code == 200
    assert "ends_at" in r.json()

    # Parent approves
    r = client.post(
        f"/api/v1/parent/reflection/{rid}/approve",
        json={"parent_note": "Thanks for being honest."},
    )
    assert r.status_code == 200

    after = client.get("/api/v1/child/state", headers=HEADERS).json()
    assert after["reflection_request"]["status"] == "approved"
    assert after["reflection_request"]["parent_note"] == "Thanks for being honest."

    # Ack drops it
    r = client.post(f"/api/v1/child/reflection/{rid}/ack", headers=HEADERS)
    assert r.status_code == 204
    after = client.get("/api/v1/child/state", headers=HEADERS).json()
    assert after["reflection_request"] is None
