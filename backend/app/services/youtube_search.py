"""YouTube Data API v3 — search for K-8 educational videos.

Searches YouTube, then ranks results by:
  1. Title relevance (topic keywords in title)
  2. Preferred educational channels (Khan Academy, CrashCourse, etc.)
  3. YouTube's default relevance
"""
from __future__ import annotations

import logging
import os
import re

import requests

log = logging.getLogger(__name__)

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

# Curated kid-friendly educational channels — results from these get a boost
_PREFERRED_CHANNELS: set[str] = {
    "khan academy",
    "crashcourse",
    "crashcourse kids",
    "scishow kids",
    "national geographic kids",
    "math antics",
    "numberblocks",
    "ted-ed",
    "freeschool",
    "homeschool pop",
    "learn bright",
    "peekaboo kidz",
    "kids academy",
    "oversimplified",
    "math with mr. j",
    "math with mr j",
    "twinkl teaching resources",
    "history kiddos",
    "social studies with the southern teach",
}


def _get_api_key() -> str:
    """Read API key at call time so hot-reloads pick up .env changes."""
    try:
        from backend.app.core.settings import settings

        if settings.youtube_api_key:
            return settings.youtube_api_key
    except Exception:
        pass
    return os.environ.get("YOUTUBE_API_KEY", "")


def _score_result(item: dict, topic_words: set[str]) -> float:
    """Score a YouTube result by relevance to topic + channel quality."""
    snippet = item.get("snippet", {})
    title = snippet.get("title", "").lower()
    channel = snippet.get("channelTitle", "").lower()

    score = 0.0

    # Title relevance: count how many topic keywords appear in the title
    matches = sum(1 for w in topic_words if w in title)
    score += matches * 10

    # Preferred educational channel bonus
    if channel in _PREFERRED_CHANNELS:
        score += 15

    # Bonus for "for kids" / "explained" / "education" in title
    edu_signals = ["for kids", "explained", "education", "learn", "lesson", "tutorial"]
    for signal in edu_signals:
        if signal in title:
            score += 3

    return score


def search_edu_videos(
    topic: str,
    grade: int = 5,
    subject: str = "",
    top_n: int = 3,
) -> list[dict]:
    """Search YouTube for kid-friendly educational videos.

    Fetches results, scores them, and returns the top_n best matches.
    Each dict has: {video_id, title, channel, thumbnail}.
    Returns empty list on failure.
    """
    api_key = _get_api_key()
    if not api_key:
        log.info("YOUTUBE_API_KEY not set, skipping video search")
        return []

    # Build a kid-friendly search query
    grade_label = f"grade {grade}" if grade <= 8 else ""
    query = f"{topic} explained for kids {grade_label} {subject}".strip()

    # Extract topic keywords for relevance scoring (drop short words)
    topic_words = {
        w.lower() for w in re.split(r"\s+", topic.strip())
        if len(w) >= 3
    }

    try:
        resp = requests.get(
            _SEARCH_URL,
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "safeSearch": "strict",
                "maxResults": 10,
                "videoEmbeddable": "true",
                "relevanceLanguage": "en",
                "key": api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items:
            log.info("No YouTube results for: %s", query)
            return []

        # Score and rank results
        scored = [(item, _score_result(item, topic_words)) for item in items]
        scored.sort(key=lambda x: x[1], reverse=True)

        results = []
        for item, score in scored[:top_n]:
            snippet = item.get("snippet", {})
            log.info(
                "YouTube match (score=%.1f): %s — %s",
                score,
                snippet.get("title", ""),
                snippet.get("channelTitle", ""),
            )
            results.append({
                "video_id": item["id"]["videoId"],
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            })
        return results
    except Exception as e:
        log.warning("YouTube search failed: %s", e)
        return []


def build_youtube_embed(video_id: str, width: int = 560, height: int = 315) -> str:
    """Return an iframe embed HTML for a YouTube video."""
    return (
        f'<iframe width="{width}" height="{height}" '
        f'src="https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1" '
        f'frameborder="0" allow="accelerometer; autoplay; clipboard-write; '
        f'encrypted-media; gyroscope; picture-in-picture" '
        f'allowfullscreen style="border-radius:12px;max-width:100%;"></iframe>'
    )
