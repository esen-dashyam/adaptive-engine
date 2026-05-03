"""Tests for Gemini reflection content generator."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.gemini_reflection import generate_reflection_content


@pytest.mark.asyncio
async def test_generate_reflection_content_parses_response() -> None:
    """Gemini returns quiz + writingPrompt; video is a server-side
    placeholder (Rick Roll) until YouTube Data API is wired."""
    fake_json = {
        "writingPrompt": "How did you feel and what could you do differently?",
        "quiz": [
            {"q": "Q1?", "options": ["a", "b", "c", "d"], "correctIndex": 0}
        ] * 5,
    }
    with patch("backend.app.services.gemini_reflection._call_gemini",
               new=AsyncMock(return_value=json.dumps(fake_json))):
        content = await generate_reflection_content(reason="kept scrolling")
    # Video is the hardcoded placeholder, not a Gemini-derived value.
    assert content.video_id == "dQw4w9WgXcQ"
    assert "placeholder" in content.video_title.lower()
    assert len(content.quiz) == 5
    assert content.writing_prompt.startswith("How did you feel")


@pytest.mark.asyncio
async def test_generate_reflection_content_strips_code_fence() -> None:
    """Gemini sometimes wraps its JSON in a ```json ... ``` fence
    despite being asked not to. The parser should still succeed."""
    fake_json = {
        "writingPrompt": "Tell me what happened.",
        "quiz": [{"q": "Q?", "options": ["a", "b", "c", "d"], "correctIndex": 1}] * 5,
    }
    fenced = "```json\n" + json.dumps(fake_json) + "\n```"
    with patch("backend.app.services.gemini_reflection._call_gemini",
               new=AsyncMock(return_value=fenced)):
        content = await generate_reflection_content(reason="hit your sister")
    assert len(content.quiz) == 5
    assert content.writing_prompt == "Tell me what happened."
