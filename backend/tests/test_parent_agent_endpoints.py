"""Smoke tests for the agent-path of /parent/chat and /parent/agent/exec.

Strategy:
- Monkeypatch GeminiAgentClient.chat to return a stub response, so we
  don't need a real Gemini API key.
- Monkeypatch settings.agent_enabled = True for the duration of the
  test so the new code path runs.
- Assert the response envelope shape (proposals/receipts/cancelled).

This is the "minimal end-to-end" test we picked over a no-test option:
it gives confidence that wiring is correct without spending time on a
full Gemini round-trip mock.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def enable_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.core import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "agent_enabled", True)
    # The route also requires gemini_api_key to be truthy.
    if not settings_mod.settings.gemini_api_key:
        monkeypatch.setattr(
            settings_mod.settings, "gemini_api_key", "test-key-for-smoke",
        )


class _StubResp:
    def __init__(self, text: str = "", tool_calls: list | None = None) -> None:
        self.text = text
        self.tool_calls = tool_calls or []


def test_agent_path_returns_envelope_with_message(
    client: TestClient,
    enable_agent: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When agent_enabled and AI returns plain text, response carries
    message + empty proposals/receipts/cancelled lists."""

    async def fake_chat(self: Any, **kwargs: Any) -> _StubResp:
        return _StubResp(text="Hi there!")

    from backend.app.services import agent_gemini
    monkeypatch.setattr(agent_gemini.GeminiAgentClient, "chat", fake_chat)

    r = client.post(
        "/api/v1/parent/chat",
        json={
            "message": "hello",
            "child_name": "Liam",
            "history": [],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["message"] == "Hi there!"
    assert body["action"] is None
    assert body["proposals"] == []
    assert body["receipts"] == []
    assert body["cancelled_proposals"] == []


def test_agent_exec_unknown_token_returns_410(client: TestClient) -> None:
    """Token not in the store returns 410 Gone."""
    r = client.post(
        "/api/v1/parent/agent/exec",
        json={"token": "nonexistent-token"},
    )
    assert r.status_code == 410
