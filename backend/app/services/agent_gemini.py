"""Gemini function-calling adapter for AgentLoop. Wraps google.genai SDK.

Implementation notes per code-review feedback:
- system_instruction (not user-turn folding) for the system prompt.
- function_response parts for tool results so the model knows which
  call each result resolves (raw text confused multi-step loops).
- asyncio.to_thread around the SDK's blocking generate_content call.
- History role mapping: any role NOT in {"agent","assistant","evlin"}
  becomes "user". The existing iOS app sends role:"parent" — that maps
  to "user". Without this normalization Gemini rejects the contents.
- fc.args is recursively converted from MapComposite to plain dict.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from backend.app.core.settings import settings


AGENT_SYSTEM_PROMPT = """You are Evlin, an AI parental copilot. The parent is having a chat with you about their child.

CURRENT KID STATE (auto-injected, may be empty if no child paired):
{state_snapshot}

DEFAULT POSTURE: listen and inform. Most parent messages are venting, asking questions, or thinking aloud. Do NOT propose actions unless one of these signals is present:

1. Parent describes a specific bad thing the child did. Examples:
   - "She hit her sister"
   - "He kept scrolling past bedtime"
   - "Liam called me a bitch at dinner"
   In that case: call propose_reflection with `reason` describing the kid's action only (avoid 'You did' literal phrasing — the downstream model will rephrase). Never lecture the parent. One empathetic sentence in your message field is plenty.

2. Parent explicitly asks for a specific action ("approve task X", "send him a reflection about Y").

3. Parent invites you to review or judge ("look at today's submissions", "what should I do about these tasks"). For invitations to review: call review_submissions, then in the next iteration propose approve_task / request_redo for individual items based on the verdicts.

4. Parent is just venting, asking questions, or making neutral observations — DO NOT propose. Just respond conversationally. Use get_kid_state if you need context for your reply.

AMBIGUITY:
- If it's a multi-child family and the parent uses a pronoun without naming, ask which kid in plain language. Do NOT pick one.
- If single-child, resolve pronouns to that child silently.
- If you need a parameter you don't have (task_id, bypass_id), ask the parent in plain language. Do NOT call the tool.

CONFIRMATION:
- Tools you call with `requires_confirm` may be staged for parent approval before they run. The parent will see a Confirm button. Don't promise the action ran in your message — say things like "Want me to ..." or describe the proposal neutrally.
- Tools without confirm execute immediately. Their effects are real.

NOT WIRED IN THIS VERSION:
- Shielding / blocking apps (e.g. "lock Instagram for 30 min") is handled by a different system. If the parent asks for that, tell them to phrase it as a direct command to Evlin and the existing flow will pick it up — do NOT try to call any tool for it.
- lock_device toggles a server-side flag but the kid app does not yet honor it visually. Mention this caveat if the parent asks for full-device locks.

EMPATHY:
When the parent describes frustration, anger, or sadness, acknowledge it before calling any tool. One sentence is enough.
"""


@dataclass
class GeminiToolCall:
    id: str           # Use call.name when SDK doesn't provide an id.
    name: str
    args: dict


@dataclass
class GeminiResponse:
    tool_calls: list[GeminiToolCall]
    text: str


def _to_plain(value: Any) -> Any:
    """Recursively convert google-genai proto-backed Map/RepeatedComposite
    structures into plain Python dicts/lists. Tools assume ordinary types.

    Order matters: MapComposite has both `items()` and `__iter__`, so we
    test the dict-like branch first, then the iterable branch. Strings
    and bytes are returned as-is.
    """
    if isinstance(value, (str, bytes)):
        return value
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if hasattr(value, "items"):
        try:
            return {k: _to_plain(v) for k, v in value.items()}
        except Exception:
            pass
    if hasattr(value, "__iter__"):
        try:
            return [_to_plain(v) for v in value]
        except Exception:
            return value
    return value


class GeminiAgentClient:
    """Real adapter calling google.genai. Tests substitute a stub
    conforming to the same `chat(**kwargs) -> GeminiResponse` contract."""

    async def chat(
        self,
        *,
        history: list[dict],
        state_snapshot: dict | None,
        user_message: str | None,
        tool_results: list[dict] | None,  # [{call_id, name, status, data?, error?}]
        tools: list[dict],
        child_name: str,
    ) -> GeminiResponse:
        from google import genai
        from google.genai import types

        # Build conversation contents.
        contents: list[types.Content] = []

        # Replay conversation history (last 10).
        for h in history[-10:]:
            raw_role = (h.get("role") or "").lower()
            # ChatViewModel uses "user" / "agent"; legacy mock uses "parent".
            # Anything not clearly an assistant role becomes "user" (Gemini
            # only accepts "user" / "model" — extra roles raise 400).
            role = (
                "model"
                if raw_role in ("agent", "assistant", "evlin")
                else "user"
            )
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=str(h.get("content", "")))],
            ))

        if user_message is not None:
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_text(
                    text=f"[Child context: {child_name}] {user_message}",
                )],
            ))

        if tool_results is not None:
            # Tool results go back as function_response parts. Gemini's SDK
            # accepts these as user-role Content; the `name` lets the model
            # match each response to the prior function_call it issued.
            for tr in tool_results:
                response_payload: dict[str, Any] = {
                    "status": tr.get("status"),
                }
                if tr.get("data") is not None:
                    response_payload["result"] = tr.get("data")
                if tr.get("error") is not None:
                    response_payload["error"] = tr.get("error")
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(
                        name=tr.get("name") or tr.get("call_id") or "unknown",
                        response=response_payload,
                    )],
                ))

        # System prompt — formatted once with state snapshot. We pass it via
        # config.system_instruction for proper system-role semantics.
        sys_text = AGENT_SYSTEM_PROMPT.format(
            state_snapshot=json.dumps(
                state_snapshot or {}, default=str, ensure_ascii=False,
            ),
        )

        client = genai.Client(api_key=settings.gemini_api_key)
        tool_decl = types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=t["parameters"],
            )
            for t in tools
        ])

        # generate_content is blocking; offload to a thread so the FastAPI
        # event loop stays responsive.
        def _call_sync():
            return client.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=sys_text,
                    temperature=0.4,
                    tools=[tool_decl],
                ),
            )

        resp = await asyncio.to_thread(_call_sync)

        tool_calls: list[GeminiToolCall] = []
        text_chunks: list[str] = []
        for cand in (resp.candidates or []):
            content = getattr(cand, "content", None)
            for part in (getattr(content, "parts", None) or []):
                fc = getattr(part, "function_call", None)
                if fc is not None and getattr(fc, "name", None):
                    tool_calls.append(GeminiToolCall(
                        id=getattr(fc, "id", "") or fc.name,
                        name=fc.name,
                        args=_to_plain(fc.args) or {},
                    ))
                txt = getattr(part, "text", None)
                if txt:
                    text_chunks.append(txt)
        return GeminiResponse(tool_calls=tool_calls, text="".join(text_chunks))
