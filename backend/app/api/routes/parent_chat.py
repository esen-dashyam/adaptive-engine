"""Parent Chat — Agentic AI endpoint for parental control commands.

POST /api/v1/parent/chat — Send a message, get AI response + optional device action
GET  /api/v1/parent/youtube-recommendations — YouTube video recommendations
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from backend.app.core.settings import settings

router = APIRouter(prefix="/parent", tags=["Parent Chat"])


class ChatRequest(BaseModel):
    message: str
    child_name: str = "Liam"
    history: list[dict[str, str]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    message: str
    reasoning: str | None = None
    action: dict[str, Any] | None = None  # e.g. {"type": "lock", "minutes": 30}


SYSTEM_PROMPT = """You are Evlin, an AI-powered parental control assistant. You help parents manage their children's screen time and digital wellbeing.

Your persona is "The Informed Sentinel" — authoritative, calm, data-driven. You speak like a high-end advisor, not a chatbot.

You can perform these actions by including a JSON action block in your response:
- Lock device: {"type": "lock", "minutes": N}
- Unlock device: {"type": "unlock"}
- Set bedtime: {"type": "bedtime", "time": "HH:MM"}
- Block apps: {"type": "block_category", "category": "games|social|entertainment"}

When the parent asks you to perform an action, include BOTH a natural response AND the action.

Response format (ALWAYS return valid JSON):
{
    "message": "Your conversational response to the parent",
    "reasoning": "Brief internal analysis/context (shown as 'Strategic Context' card)",
    "action": null or {"type": "...", ...}
}

Rules:
- Be concise but insightful
- Reference behavioral patterns and data when relevant
- When giving advice, use numbered steps (01, 02, etc.)
- Use clinical/strategic language, not casual
- The child's name is provided in each message
- If no action is needed, set action to null
- ALWAYS return valid JSON, nothing else"""


@router.post("/chat", summary="Parent chat with Evlin AI")
async def parent_chat(req: ChatRequest) -> ChatResponse:
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="Gemini API key not configured")

    # Build conversation as a single prompt with history
    history_text = ""
    for h in req.history[-10:]:
        role = "Parent" if h.get("role") == "parent" else "Evlin"
        history_text += f"{role}: {h.get('content', '')}\n"

    user_msg = f"{history_text}Parent: [Child: {req.child_name}] {req.message}"
    full_prompt = f"{SYSTEM_PROMPT}\n\n---\nConversation:\n{user_msg}\n\nRespond as Evlin in JSON format:"

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            ),
        )

        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]

        data = json.loads(text)
        return ChatResponse(
            message=data.get("message", "I apologize, I couldn't process that request."),
            reasoning=data.get("reasoning"),
            action=data.get("action"),
        )

    except json.JSONDecodeError as e:
        logger.error("Chat JSON parse failed: {} — raw: {}", e, text[:200])
        raise HTTPException(status_code=500, detail="Failed to parse AI response")
    except Exception as e:
        logger.error("Chat failed: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/youtube-recommendations", summary="Get YouTube video recommendations")
async def youtube_recommendations(
    query: str = Query(default="parenting screen time management children"),
    max_results: int = Query(default=5, le=10),
) -> list[dict]:
    """Search YouTube for parenting/screen-time related videos."""
    try:
        from backend.app.services.youtube_search import search_edu_videos

        results = search_edu_videos(
            topic=query,
            top_n=max_results,
        )

        return [
            {
                "video_id": v.get("video_id", ""),
                "title": v.get("title", ""),
                "channel": v.get("channel", ""),
                "thumbnail": v.get("thumbnail", ""),
            }
            for v in results
        ]
    except Exception as e:
        logger.error("YouTube search failed: {}", e)
        raise HTTPException(status_code=500, detail=str(e))
