"""Scheduler — Game config generation endpoints.

POST /api/v1/scheduler/games/generate-config  — Generate game config via Gemini
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from backend.app.core.settings import settings

router = APIRouter(prefix="/scheduler/games", tags=["Scheduler — Games"])


class GameConfigRequest(BaseModel):
    game_type: str = "star_blaster"  # star_blaster | tower_defense | quiz_dungeon | vocab_match
    topic: str = "Fractions"
    subject: str = "Math"
    grade: int = 5
    num_questions: int = 5


GAME_PROMPTS = {
    "star_blaster": """Generate {num_questions} multiple-choice questions about "{topic}" for a Grade {grade} {subject} student.

Return a JSON object:
{{
  "title": "Star Blaster: {topic}",
  "questions": [
    {{
      "question": "question text",
      "correct": "correct answer",
      "wrong": ["wrong1", "wrong2", "wrong3"]
    }}
  ]
}}

Rules:
- Questions should be age-appropriate for Grade {grade}
- Each question has exactly 1 correct answer and 3 wrong answers
- Keep answers short (1-4 words)
- Progressively harder
- Return ONLY valid JSON""",

    "vocab_match": """Generate {num_questions} vocabulary term-definition pairs about "{topic}" for a Grade {grade} {subject} student.

Return a JSON object:
{{
  "title": "Vocabulary Match: {topic}",
  "subject": "{subject}",
  "grade": {grade},
  "pairs": [
    {{"term": "vocabulary word", "definition": "short definition"}}
  ]
}}

Rules:
- Terms and definitions should be age-appropriate
- Definitions should be concise (under 10 words)
- Return ONLY valid JSON""",

    "tower_defense": """Generate {num_questions} multiple-choice questions about "{topic}" for a Grade {grade} {subject} student, formatted for a tower defense strategy game.

Return a JSON object:
{{
  "title": "Tower Defense: {topic}",
  "waves": [
    {{
      "question": "question text",
      "correct": "correct answer",
      "wrong": ["wrong1", "wrong2", "wrong3"],
      "enemies": 5,
      "enemy_speed": 60
    }}
  ]
}}

Rules:
- Age-appropriate for Grade {grade}
- Each question has exactly 1 correct answer and 3 wrong answers
- Keep answers short (1-4 words)
- Progressively harder: early waves have fewer/slower enemies, later waves have more/faster
- enemies range from 3 to 8, enemy_speed from 50 to 100
- Return ONLY valid JSON""",

    "quiz_dungeon": """Generate {num_questions} multiple-choice questions about "{topic}" for a Grade {grade} {subject} student, formatted for a dungeon RPG game.

Return a JSON object:
{{
  "title": "Quiz Dungeon: {topic}",
  "rooms": [
    {{
      "question": "question text",
      "correct": "correct answer",
      "wrong": ["wrong1", "wrong2", "wrong3"],
      "reward": "sword",
      "reward_name": "a cool weapon name related to the topic"
    }}
  ]
}}

Rewards should cycle through: sword, shield, potion, helmet, boots
Rules:
- Age-appropriate for Grade {grade}
- Keep answers short
- Return ONLY valid JSON""",
}


@router.post("/generate-config", summary="Generate game config via Gemini")
async def generate_game_config(req: GameConfigRequest) -> dict:
    """Generate a game config JSON based on topic, subject, grade using Gemini."""
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="Gemini API key not configured")

    prompt_template = GAME_PROMPTS.get(req.game_type)
    if not prompt_template:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown game type: {req.game_type}. Available: {', '.join(GAME_PROMPTS.keys())}",
        )

    prompt = prompt_template.format(
        topic=req.topic,
        subject=req.subject,
        grade=req.grade,
        num_questions=req.num_questions,
    )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.8,
                response_mime_type="application/json",
            ),
        )
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        config = json.loads(text)
        return config

    except json.JSONDecodeError as e:
        logger.error("Game config JSON parse failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to parse game config from AI")
    except Exception as e:
        logger.error("Game config generation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
