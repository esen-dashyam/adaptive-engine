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

# Placeholder video — Gemini-generated quiz + writing prompt are tailored
# to the parent's reason, but the YouTube embed is currently hard-coded to
# Rick Astley until we wire YouTube Data API search (Phase 9 v2).
_PLACEHOLDER_VIDEO_ID = "dQw4w9WgXcQ"
_PLACEHOLDER_VIDEO_TITLE = "Why rest time matters for your brain (placeholder)"

PROMPT_TEMPLATE = """\
You are designing a reflection exercise for an 8–12 year old child after \
the following issue (described by their parent):

  {reason}

Generate strict JSON with two keys, no prose, no code fences:

- "quiz": EXACTLY 5 multiple-choice questions that probe the child's \
understanding of *why what they did was a problem* and *what better \
choices look like*. Tone should be calm and non-shaming, not lecturing. \
Make the questions specific to the issue above — do NOT use generic \
"screen time" filler. Each question is an object with "q" (string), \
"options" (array of 4 strings), "correctIndex" (integer 0..3). Every \
question must have exactly one clearly correct answer.

- "writingPrompt": a 1-2 sentence prompt asking the child to reflect on \
*this specific situation* and what they could do differently next time. \
Use the second person ("you") and reference the issue concretely.

Output JSON only.
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


def _strip_code_fence(raw: str) -> str:
    """Gemini sometimes wraps JSON in ```json ... ``` despite being asked
    not to. Strip it so json.loads succeeds."""
    s = raw.strip()
    if s.startswith("```"):
        # drop the opening fence (with optional language tag)
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        # drop closing fence
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


async def generate_reflection_content(*, reason: str) -> ReflectionContent:
    raw = await _call_gemini(PROMPT_TEMPLATE.format(reason=reason))
    parsed = json.loads(_strip_code_fence(raw))
    if len(parsed.get("quiz", [])) != 5:
        raise ValueError("Gemini returned wrong number of quiz questions")
    return ReflectionContent(
        video_id=_PLACEHOLDER_VIDEO_ID,
        video_title=_PLACEHOLDER_VIDEO_TITLE,
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
