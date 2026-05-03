"""Gemini-based reflection content generator (spec §10).

Public entry: ``generate_reflection_content(reason)`` returning a structured
``ReflectionContent`` ready to persist via ``BigKidStore.trigger_reflection``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from backend.app.core.settings import settings


def _gemini_key() -> str:
    """Read at call time (not import time) so test fixtures and runtime
    overrides take effect. Mirrors how the rest of the project reads
    `settings.gemini_api_key` — supports both real env vars and a
    `.env` file picked up by pydantic-settings."""
    return settings.gemini_api_key or ""

# Placeholder video — Gemini-generated quiz + writing prompt are tailored
# to the parent's reason, but the YouTube embed is currently hard-coded to
# Rick Astley until we wire YouTube Data API search (Phase 9 v2).
_PLACEHOLDER_VIDEO_ID = "dQw4w9WgXcQ"
_PLACEHOLDER_VIDEO_TITLE = "Why rest time matters for your brain (placeholder)"

PROMPT_TEMPLATE = """\
You are designing a reflection exercise for an 8–12 year old child after \
the following issue, described by their parent:

  {reason}

The parent's wording above may be a sentence fragment, contain rude \
language, or use grammar that doesn't fit a child-facing screen. You \
will rephrase it on their behalf.

Generate strict JSON with three keys, no prose, no code fences:

- "displayReason": ONE complete second-person sentence stating what the \
child did wrong, in calm and non-shaming language a 10-year-old will \
understand. End with a period. If the parent used a slur or rude word, \
soften it without changing the meaning. The sentence should read as \
clean grammar on its own — do not start with "You did" mechanically.

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
    display_reason: str    # kid-facing rephrasing of the parent's reason


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
    display = (parsed.get("displayReason") or "").strip()
    if not display:
        raise ValueError("Gemini omitted displayReason")
    return ReflectionContent(
        video_id=_PLACEHOLDER_VIDEO_ID,
        video_title=_PLACEHOLDER_VIDEO_TITLE,
        quiz=[QuizSeed(q=q["q"], options=q["options"],
                       correct_index=int(q["correctIndex"])) for q in parsed["quiz"]],
        writing_prompt=parsed["writingPrompt"],
        display_reason=display,
    )


async def _call_gemini(prompt: str) -> str:
    key = _gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    # Use the project-wide default model from settings (currently
    # `gemini-2.5-flash`) so we follow the rest of the codebase when the
    # model is rotated. `gemini-1.5-flash` was hardcoded here originally
    # but Google deprecated that name and the endpoint returns 404.
    model = settings.gemini_model or "gemini-2.5-flash"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
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
