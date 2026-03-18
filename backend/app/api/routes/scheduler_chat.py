"""Scheduler — lightweight AI chat for Evlin frontend.

POST /api/v1/scheduler/chat/ask   — Send a message, get AI response
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from backend.app.core.settings import settings

router = APIRouter(prefix="/scheduler/chat", tags=["Scheduler — Chat"])


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class AskRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    course_context: str = ""  # optional: injected when opened from Courses page


class AskResponse(BaseModel):
    reply: str


_BASE_SYSTEM = """You are Evlin, a friendly and encouraging AI tutor for homeschool students.

Guidelines:
- Use clear, age-appropriate language
- Break complex topics into simple steps
- Give examples when explaining concepts
- If the student asks about homework, guide them toward the answer instead of giving it directly
- Be warm and supportive — celebrate effort and progress
- For math: show step-by-step solutions
- Keep responses concise but thorough
- Use English by default, but respond in the student's language if they write in another language"""


@router.post("/shorten-labels", summary="Shorten gap descriptions to concise topic labels")
async def shorten_labels(body: dict) -> dict:
    """Take a list of verbose standard descriptions and return short 2-4 word labels.

    Body: { "descriptions": ["Use place value understanding to round...", ...] }
    Returns: { "labels": ["Rounding whole numbers", ...] }
    """
    descriptions = body.get("descriptions", [])
    if not descriptions:
        return {"labels": []}
    if not settings.gemini_api_key:
        # Fallback: first 5 words
        return {"labels": [" ".join(d.split()[:5]) for d in descriptions]}

    try:
        from google import genai
        from google.genai import types
        import json

        numbered = "\n".join(f"{i+1}. {d}" for i, d in enumerate(descriptions))
        prompt = f"""Convert each educational standard description below into a SHORT topic name (2-4 words).
Examples:
- "Use place value understanding to round whole numbers to the nearest 10 or 100." → "Rounding whole numbers"
- "Apply and extend previous understandings of division to divide unit fractions" → "Fraction division"
- "Gather and make sense of information to describe that light travels" → "Light & travel"
- "Use numbers expressed in the form of a single digit times an integer power of 10" → "Scientific notation"

Now convert these:
{numbered}

Return a JSON array of strings, one per input, in the same order. ONLY the JSON array, nothing else."""

        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        labels = json.loads(text)
        if isinstance(labels, list) and len(labels) == len(descriptions):
            return {"labels": labels}
    except Exception as e:
        logger.warning("shorten-labels failed: %s", e)

    # Fallback
    return {"labels": [" ".join(d.split()[:4]) for d in descriptions]}


@router.post("/ask", summary="Ask AI a question")
async def ask_ai(req: AskRequest) -> AskResponse:
    """Send a message to the AI tutor and get a response."""
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="AI is not configured (missing GEMINI_API_KEY)")

    try:
        from google import genai
        from google.genai import types

        # Build system prompt
        system = _BASE_SYSTEM
        if req.course_context:
            system += f"\n\nThe student is currently studying these courses:\n{req.course_context}\nUse this context to give relevant, curriculum-aligned answers."

        # Build conversation
        contents: list[types.Content] = []
        for msg in req.history[-20:]:  # keep last 20 messages
            contents.append(types.Content(
                role="user" if msg.role == "user" else "model",
                parts=[types.Part.from_text(text=msg.content)],
            ))
        contents.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=req.message)],
        ))

        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.7,
            ),
        )

        reply = resp.text.strip()
        if not reply:
            reply = "I'm sorry, I couldn't generate a response. Could you rephrase your question?"

        return AskResponse(reply=reply)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("AI chat failed: %s", e)
        raise HTTPException(status_code=500, detail=f"AI error: {e}")
