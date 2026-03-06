"""
Gemini service for question generation and concept explanation.

Priority:
  1. google-genai SDK with GEMINI_API_KEY  (get free key at aistudio.google.com)
  2. Vertex AI SDK with GCP_PROJECT_ID     (full GCP project + service account)
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from loguru import logger

from backend.app.core.settings import settings


class GeminiService:
    """Gemini integration for exercise generation and concept chat."""

    def __init__(self):
        self._model = None
        self._chat_sessions: dict[str, Any] = {}

    def _get_model(self):
        """
        Lazy-init Gemini model.
        Returns None if neither GEMINI_API_KEY nor GCP_PROJECT_ID is set.
        """
        if self._model is not None:
            return self._model

        # Option 1: google-genai API key
        if settings.gemini_api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=settings.gemini_api_key)
                self._model = genai.GenerativeModel(settings.gemini_model)
                logger.info(f"Gemini API key initialised: {settings.gemini_model}")
                return self._model
            except Exception as exc:
                logger.warning(f"google-genai init failed: {exc}")

        # Option 2: Vertex AI
        if settings.gcp_project_id:
            try:
                import vertexai
                from vertexai.generative_models import GenerativeModel
                vertexai.init(project=settings.gcp_project_id, location=settings.gcp_region)
                self._model = GenerativeModel(settings.vertex_model)
                logger.info(f"Vertex AI Gemini initialised: {settings.vertex_model}")
                return self._model
            except Exception as exc:
                logger.warning(f"Vertex AI init failed: {exc}")

        logger.warning(
            "Gemini not configured. Add GEMINI_API_KEY to .env "
            "(free key at https://aistudio.google.com/app/apikey)"
        )
        return None

    def generate_content(self, prompt: str) -> str | None:
        """Generate text from a prompt. Returns raw text or None."""
        model = self._get_model()
        if not model:
            return None
        try:
            resp = model.generate_content(prompt)
            return resp.text
        except Exception as exc:
            logger.warning(f"Gemini generate_content failed: {exc}")
            return None

    def start_chat_session(
        self,
        student_id: str,
        node_identifier: str,
        node_code: str,
        node_description: str,
        grade: str,
        subject: str,
        nano_weight: float = 0.0,
    ) -> dict[str, Any]:
        """Start a Gemini tutoring chat session for a concept."""
        session_id = str(uuid.uuid4())
        grade_num = grade.upper().replace("K", "")
        subject_name = "Mathematics" if subject.lower() == "math" else "English Language Arts"

        system_ctx = (
            f"You are a patient, encouraging tutor explaining {subject_name} "
            f"to a Grade {grade_num} student.\n"
            f"Standard: {node_code} — {node_description}\n"
            f"Student understanding: {nano_weight}/100\n"
            "Rules: use simple age-appropriate language, start with a real-world example, "
            "break into small steps, ask one comprehension check question at the end, "
            "keep responses under 3 paragraphs."
        )
        initial = f"Please explain: {node_code}: {node_description}"

        model = self._get_model()
        if model:
            try:
                chat = model.start_chat(history=[])
                chat.send_message(system_ctx)
                resp = chat.send_message(initial)
                self._chat_sessions[session_id] = {
                    "chat": chat,
                    "student_id": student_id,
                    "node_identifier": node_identifier,
                    "node_code": node_code,
                    "grade": grade,
                    "subject": subject,
                    "messages": [{"role": "assistant", "content": resp.text}],
                }
                return {"session_id": session_id, "node_code": node_code, "explanation": resp.text}
            except Exception as exc:
                logger.warning(f"Gemini chat init failed: {exc}")

        return {
            "session_id": session_id,
            "node_code": node_code,
            "explanation": (
                f"I'd explain {node_code}: {node_description} — "
                "but the AI service is unavailable. Please set GEMINI_API_KEY in .env."
            ),
        }

    def continue_chat(self, session_id: str, user_message: str) -> dict[str, Any]:
        """Continue an existing tutoring chat session."""
        session = self._chat_sessions.get(session_id)
        if not session:
            return {"error": "Session not found", "session_id": session_id}
        chat = session.get("chat")
        if chat:
            try:
                resp = chat.send_message(user_message)
                session["messages"].append({"role": "user", "content": user_message})
                session["messages"].append({"role": "assistant", "content": resp.text})
                return {"session_id": session_id, "response": resp.text,
                        "message_count": len(session["messages"])}
            except Exception as exc:
                logger.warning(f"Gemini chat continue failed: {exc}")
        return {"session_id": session_id, "response": "AI service unavailable.",
                "message_count": len(session.get("messages", []))}

    def parse_json_response(self, text: str, array: bool = False) -> Any:
        """Strip markdown fences and parse JSON from LLM response."""
        text = text.strip()
        if "```" in text:
            text = "\n".join(l for l in text.split("\n") if not l.strip().startswith("```")).strip()
        if array:
            si, ei = text.find("["), text.rfind("]") + 1
        else:
            si, ei = text.find("{"), text.rfind("}") + 1
        if si >= 0 and ei > si:
            try:
                return json.loads(text[si:ei])
            except json.JSONDecodeError as exc:
                logger.warning(f"JSON parse error: {exc}")
        return None
