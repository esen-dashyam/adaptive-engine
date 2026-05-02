"""Tests for Gemini reflection content generator."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.gemini_reflection import generate_reflection_content


@pytest.mark.asyncio
async def test_generate_reflection_content_parses_response() -> None:
    fake_json = {
        "videoQuery": "screen time and brain rest for kids",
        "writingPrompt": "How did you feel and what could you do differently?",
        "quiz": [
            {"q": "Q1?", "options": ["a", "b", "c", "d"], "correctIndex": 0}
        ] * 5,
    }
    with patch("backend.app.services.gemini_reflection._call_gemini",
               new=AsyncMock(return_value=json.dumps(fake_json))):
        with patch("backend.app.services.gemini_reflection._search_youtube",
                   new=AsyncMock(return_value=("yt123", "Some video title"))):
            content = await generate_reflection_content(reason="kept scrolling")
    assert content.video_id == "yt123"
    assert content.video_title == "Some video title"
    assert len(content.quiz) == 5
    assert content.writing_prompt.startswith("How did you feel")
