"""
Vertex AI LLM client for all agents.

Priority order:
  1. Vertex AI via Application Default Credentials (ADC) — uses gcloud auth
     Calls generativelanguage.googleapis.com with Bearer token from ADC.
  2. google.generativeai — uses GEMINI_API_KEY from settings.
  3. Raises RuntimeError with setup instructions if neither works.

Usage:
    from backend.app.agents.vertex_llm import VertexLLM
    llm = VertexLLM()
    text = llm.generate("Write a Grade 3 math question about fractions.")
    data = llm.generate_json("Return a JSON array of 3 questions...")
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests
from loguru import logger

from backend.app.core.settings import settings


_GENAI_REST_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
_VERTEX_REST_URL = (
    "https://{region}-aiplatform.googleapis.com/v1/projects/{project}"
    "/locations/{region}/publishers/google/models/{model}:generateContent"
)


class VertexLLM:
    """
    Unified LLM client.  Tries Vertex AI via ADC first, falls back to API key.
    Thread-safe: each call refreshes credentials as needed.
    """

    def __init__(self):
        self._adc_creds = None
        self._genai_model = None

    # ── credential helpers ────────────────────────────────────────────────────

    def _get_adc_token(self) -> str | None:
        """Refresh and return a Bearer token from Application Default Credentials."""
        try:
            import google.auth
            from google.auth.transport.requests import Request

            if self._adc_creds is None:
                self._adc_creds, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            if not self._adc_creds.valid:
                self._adc_creds.refresh(Request())
            return self._adc_creds.token
        except Exception as exc:
            logger.debug(f"ADC token fetch failed: {exc}")
            return None

    # ── generation via REST ───────────────────────────────────────────────────

    def _call_genai_rest(self, prompt: str, model: str) -> str | None:
        """Call generativelanguage.googleapis.com with a Bearer token (ADC)."""
        token = self._get_adc_token()
        if not token:
            return None
        url = _GENAI_REST_URL.format(model=model)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192},
        }
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=60,
            )
            if resp.status_code == 200:
                data = resp.json()
                return (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text")
                )
            logger.debug(f"Generative Language REST {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            logger.debug(f"Generative Language REST call failed: {exc}")
        return None

    def _call_vertex_rest(self, prompt: str, model: str) -> str | None:
        """Call Vertex AI aiplatform REST endpoint with ADC Bearer token."""
        token = self._get_adc_token()
        if not token:
            return None
        url = _VERTEX_REST_URL.format(
            region=settings.gcp_region,
            project=settings.gcp_project_id,
            model=model,
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192},
        }
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=60,
            )
            if resp.status_code == 200:
                data = resp.json()
                return (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text")
                )
            logger.debug(f"Vertex REST {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            logger.debug(f"Vertex REST call failed: {exc}")
        return None

    def _call_with_payload(self, url: str, payload: dict) -> str | None:
        """POST a pre-built Gemini payload to any REST endpoint with ADC Bearer token."""
        token = self._get_adc_token()
        if not token:
            return None
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=90,
            )
            if resp.status_code == 200:
                data = resp.json()
                return (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text")
                )
            logger.debug(f"REST {resp.status_code} ({url.split('/')[-1]}): {resp.text[:200]}")
        except Exception as exc:
            logger.debug(f"REST call failed: {exc}")
        return None

    def chat(
        self,
        system: str,
        history: list[dict[str, str]],
        message: str,
        model: str | None = None,
    ) -> str:
        """
        Multi-turn conversation with a system instruction.

        history items: {"role": "user" | "assistant", "content": "..."}
        Uses Gemini's native systemInstruction + multi-turn contents format.
        Falls back to formatted single prompt if REST fails.
        """
        chat_model = model or settings.gemini_model

        contents = []
        for msg in history:
            gemini_role = "model" if msg["role"] == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": msg["content"]}]})
        contents.append({"role": "user", "parts": [{"text": message}]})

        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
        }

        if settings.gcp_project_id:
            text = self._call_with_payload(_GENAI_REST_URL.format(model=chat_model), payload)
            if text:
                logger.debug(f"Chat: used GenAI REST ({chat_model})")
                return text

        if settings.gcp_project_id:
            for m in [chat_model, settings.gemini_model]:
                url = _VERTEX_REST_URL.format(
                    region=settings.gcp_region,
                    project=settings.gcp_project_id,
                    model=m,
                )
                text = self._call_with_payload(url, payload)
                if text:
                    logger.debug(f"Chat: used Vertex REST ({m})")
                    return text

        # Fallback: flatten to a single prompt
        conv = [f"[System]\n{system}"]
        for msg in history[-10:]:
            role = "Student" if msg["role"] == "user" else "Tutor"
            conv.append(f"[{role}]\n{msg['content']}")
        conv.append(f"[Student]\n{message}\n[Tutor]")
        return self.generate("\n\n".join(conv))

    def _call_sdk(self, prompt: str) -> str | None:
        """Call google.generativeai SDK with API key."""
        if not settings.gemini_api_key:
            return None
        try:
            import google.generativeai as genai
            if self._genai_model is None:
                genai.configure(api_key=settings.gemini_api_key)
                self._genai_model = genai.GenerativeModel(settings.gemini_model)
            resp = self._genai_model.generate_content(prompt)
            return resp.text
        except Exception as exc:
            logger.debug(f"google.generativeai SDK call failed: {exc}")
            return None

    # ── public API ────────────────────────────────────────────────────────────

    def generate(self, prompt: str) -> str:
        """
        Generate text from a prompt.
        Raises RuntimeError if all backends fail.
        """
        flash_model = settings.gemini_model  # e.g. "gemini-2.5-flash"

        # 1. Try Generative Language REST (ADC token, no API key needed)
        if settings.gcp_project_id:
            text = self._call_genai_rest(prompt, flash_model)
            if text:
                logger.debug("VertexLLM: used Generative Language REST (ADC)")
                return text

        # 2. Try Vertex AI REST (ADC token, aiplatform endpoint)
        if settings.gcp_project_id:
            for model in [flash_model, "gemini-1.5-flash-001", "gemini-1.5-pro-001"]:
                text = self._call_vertex_rest(prompt, model)
                if text:
                    logger.debug(f"VertexLLM: used Vertex AI REST ({model})")
                    return text

        # 3. Try google.generativeai SDK (API key)
        text = self._call_sdk(prompt)
        if text:
            logger.debug("VertexLLM: used google.generativeai SDK (API key)")
            return text

        raise RuntimeError(
            "No LLM backend available. "
            "Either set GEMINI_API_KEY in .env (get one at https://aistudio.google.com/app/apikey) "
            "or run: gcloud auth application-default login  then set GCP_PROJECT_ID=homeschoollms in .env"
        )

    def generate_json(self, prompt: str) -> Any:
        """
        Generate text and parse as JSON (dict or list).
        - Unwraps dict wrappers like {"questions": [...]} automatically.
        - Retries once if the first attempt returns None or a non-list dict.
        Returns None on parse failure.
        """
        full_prompt = (
            prompt
            + "\n\nIMPORTANT: Return ONLY valid JSON. No markdown fences, no commentary."
        )
        for attempt in range(2):
            text = self.generate(full_prompt)
            result = self._parse_json(text)

            # Unwrap common dict wrappers returned by the model
            if isinstance(result, dict):
                for key in ("questions", "items", "data", "results", "assessment", "exercises"):
                    if isinstance(result.get(key), list):
                        result = result[key]
                        break

            if result is not None:
                return result

            if attempt == 0:
                logger.warning("generate_json: first attempt returned None, retrying…")

        return None

    @staticmethod
    def _parse_json(text: str) -> Any:
        """Strip markdown fences and parse JSON. Tries array first, then object."""
        if not text:
            return None
        text = text.strip()
        if "```" in text:
            lines = [l for l in text.split("\n") if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        for start, end in [("[", "]"), ("{", "}")]:
            si = text.find(start)
            ei = text.rfind(end) + 1
            if si >= 0 and ei > si:
                try:
                    return json.loads(text[si:ei])
                except json.JSONDecodeError:
                    pass
        return None


# module-level singleton
_llm: VertexLLM | None = None


def get_llm() -> VertexLLM:
    global _llm
    if _llm is None:
        _llm = VertexLLM()
    return _llm
