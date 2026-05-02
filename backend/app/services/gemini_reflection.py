"""Gemini-based reflection content generator (spec §10).

Public entry: ``generate_reflection_content(reason)`` returning a structured
``ReflectionContent`` ready to persist via ``BigKidStore.trigger_reflection``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx


GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY", "")

PROMPT_TEMPLATE = """\
For an 8–12 year old, generate reflection content for this issue: {reason}.
Return strict JSON with three keys:
- "videoQuery": a single sentence YouTube search for an age-appropriate \
educational video on the underlying topic
- "quiz": an array of EXACTLY 5 multiple-choice questions, each with \
"q" (string), "options" (array of 4 strings), "correctIndex" (integer 0..3)
- "writingPrompt": a 1-2 sentence prompt asking the kid to reflect on their \
behaviour and what they could do differently.
Only output JSON. No prose, no code fences.
"""


@dataclass
class QuizSeed:
    q: str
    options: list[str]
    correct_index: int


@dataclass
class ReflectionContent:
    video_id: str
    video_title: str
    quiz: list[QuizSeed]
    writing_prompt: str


async def generate_reflection_content(*, reason: str) -> ReflectionContent:
    raw = await _call_gemini(PROMPT_TEMPLATE.format(reason=reason))
    parsed = json.loads(raw)
    if len(parsed["quiz"]) != 5:
        raise ValueError("Gemini returned wrong number of quiz questions")
    video_id, video_title = await _search_youtube(parsed["videoQuery"])
    return ReflectionContent(
        video_id=video_id, video_title=video_title,
        quiz=[QuizSeed(q=q["q"], options=q["options"],
                       correct_index=int(q["correctIndex"])) for q in parsed["quiz"]],
        writing_prompt=parsed["writingPrompt"],
    )


async def _call_gemini(prompt: str) -> str:
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY missing")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, json=body)
        r.raise_for_status()
        data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


async def _search_youtube(query: str) -> tuple[str, str]:
    if not YOUTUBE_KEY:
        raise RuntimeError("YOUTUBE_API_KEY missing")
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {"key": YOUTUBE_KEY, "q": query, "part": "snippet",
              "maxResults": 1, "type": "video", "safeSearch": "strict"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    if not data.get("items"):
        raise RuntimeError("YouTube returned no results")
    item = data["items"][0]
    return item["id"]["videoId"], item["snippet"]["title"]
