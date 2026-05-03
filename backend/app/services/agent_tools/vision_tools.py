"""Multimodal tools — review_submissions loads each evidence photo and
sends it to Gemini with per-task vision prompts."""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx
from loguru import logger

from backend.app.core.settings import settings
from backend.app.services.agent_tools.decorator import tool, ToolResult, GLOBAL_REGISTRY
from backend.app.services.bigkid_store import get_store as get_bigkid_store


REVIEW_PROMPT = """\
You are reviewing photo evidence a child submitted to complete a chore.
For each item, decide whether the photo plausibly shows the chore done.
Be fair — kids submit imperfect photos. Approve unless clearly off.

For each item, output a JSON object:
{
  "task_id": "<uuid>",
  "looks_done": true | false,
  "confidence": 0.0-1.0,
  "note": "<one-line rationale>",
  "recommend_action": "approve" | "redo"
}

Output a JSON array of these objects in the same order as the input items.
"""


@tool(
    name="review_submissions",
    description=(
        "Look at photos for the kid's currently-submitted tasks and produce a "
        "per-task verdict {looks_done, confidence, note, recommend_action}. "
        "Call this when the parent asks you to review or judge submissions. "
        "Multimodal — slow + expensive — don't call speculatively."
    ),
    requires_confirm=False,
    danger="low",
    registry=GLOBAL_REGISTRY,
)
async def review_submissions(child_id: UUID) -> ToolResult:
    state = get_bigkid_store().get_state(child_id)
    pending = [
        t for t in state.tasks
        if t.status.value == "submitted" and t.phase.value == "submitted"
        and t.evidence_photo_url
    ]
    if not pending:
        return ToolResult(public={"verdicts": []}, public_summary="No pending submissions")

    items: list[dict[str, Any]] = []
    for t in pending:
        try:
            photo = await _fetch_photo_bytes(t.evidence_photo_url)
        except Exception as exc:
            logger.warning("review_submissions fetch failed for {}: {}", t.id, exc)
            continue
        items.append({
            "task_id": str(t.id),
            "title": t.title,
            "description": t.description,
            "kid_note": t.evidence_note,
            "photo_bytes": photo,
        })

    verdicts = await _call_multimodal(REVIEW_PROMPT, items)
    return ToolResult(
        public={"verdicts": verdicts},
        public_summary=f"Reviewed {len(verdicts)} submission(s)",
    )


async def _fetch_photo_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


async def _call_multimodal(prompt: str, items: list[dict]) -> list[dict]:
    """Call Gemini with photo bytes + per-item context. Returns list of
    verdict dicts in input order. v1 implementation — refine prompt later."""
    from google import genai
    from google.genai import types

    if not items:
        return []

    client = genai.Client(api_key=settings.gemini_api_key)

    parts: list[Any] = [types.Part.from_text(text=prompt)]
    for it in items:
        parts.append(types.Part.from_text(text=json.dumps({
            "task_id": it["task_id"],
            "title": it["title"],
            "description": it["description"],
            "kid_note": it.get("kid_note"),
        })))
        parts.append(types.Part.from_bytes(data=it["photo_bytes"], mime_type="image/jpeg"))

    resp = client.models.generate_content(
        model=settings.gemini_model,
        contents=parts,
        config=types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )

    text = (resp.text or "[]").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text)
