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

USING TOOLS:
The state snapshot above includes a `child_id` UUID and per-task `id`s.
When you call any tool that needs `child_id` or `task_id`, copy the
exact UUID string from the snapshot — DO NOT ask the parent for it
and DO NOT make one up. If the snapshot is empty (no child paired),
do not call tools that require an id; explain politely instead.

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
        prior_tool_calls: list[Any] | None = None,  # Replay of last turn's function calls
        tools: list[dict],
        child_name: str,
    ) -> GeminiResponse:
        from google import genai
        from google.genai import types

        # Build conversation contents.
        contents: list[types.Content] = []

        # Replay conversation history (last 10), but apply Gemini's strict
        # turn-alternation requirements when tools are enabled:
        #   - First turn must be role="user"
        #   - No consecutive same-role turns
        #   - No empty parts
        # The iOS app's seed conversation begins with three "agent" bubbles,
        # which fails this validator with a misleading "function call turn"
        # error message because Gemini's tool-mode internally validates the
        # transcript with function-call assumptions.
        cleaned: list[tuple[str, str]] = []  # (role, text) after filtering
        for h in history[-10:]:
            text = str(h.get("content", "")).strip()
            if not text:
                continue
            raw_role = (h.get("role") or "").lower()
            role = (
                "model"
                if raw_role in ("agent", "assistant", "evlin")
                else "user"
            )
            if cleaned and cleaned[-1][0] == role:
                # Merge consecutive same-role turns into one content block.
                prev_role, prev_text = cleaned[-1]
                cleaned[-1] = (prev_role, f"{prev_text}\n\n{text}")
            else:
                cleaned.append((role, text))
        # Drop leading model turns (Gemini expects user-first).
        while cleaned and cleaned[0][0] == "model":
            cleaned.pop(0)

        # If we have a new user_message, merge it onto the last user-role
        # entry (if any) so we don't emit two consecutive user turns.
        if user_message is not None:
            new_user_text = f"[Child context: {child_name}] {user_message}"
            if cleaned and cleaned[-1][0] == "user":
                prev_role, prev_text = cleaned[-1]
                cleaned[-1] = (prev_role, f"{prev_text}\n\n{new_user_text}")
            else:
                cleaned.append(("user", new_user_text))

        for role, text in cleaned:
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=text)],
            ))

        if tool_results is not None:
            # Gemini contract requires alternation:
            #   user → model(function_call) → user(function_response) → model
            # Replay the prior turn's function_call(s) as a model-role
            # Content BEFORE we attach the function_response parts; without
            # this the SDK either 400s or treats the response as a fresh
            # message and re-emits the same call (driving the loop to its
            # iteration cap).
            if prior_tool_calls:
                contents.append(types.Content(
                    role="model",
                    parts=[
                        types.Part.from_function_call(
                            name=fc.name, args=fc.args or {},
                        )
                        for fc in prior_tool_calls
                    ],
                ))
            # Tool results go back as function_response parts. The `name`
            # lets the model match each response to the prior function_call
            # it issued.
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

        # DEBUG: attach a structured request-shape summary to any
        # exception we raise — surfaces in /parent/chat's debug envelope.
        def _shape_summary() -> str:
            roles = [getattr(c, "role", "?") for c in contents]
            part_types = []
            for c in contents:
                row = []
                for p in (getattr(c, "parts", None) or []):
                    if getattr(p, "function_call", None):
                        row.append(f"fcall({p.function_call.name})")
                    elif getattr(p, "function_response", None):
                        row.append(f"fresp({p.function_response.name})")
                    elif getattr(p, "text", None) is not None:
                        row.append(f"text({len(p.text)}c)")
                    else:
                        row.append("?")
                part_types.append("|".join(row))
            tool_names = [
                fd.name
                for fd in (tool_decl.function_declarations or [])
            ]
            return (
                f"contents_len={len(contents)} roles={roles} "
                f"parts_per_content={part_types} "
                f"sys_instr_len={len(sys_text)} "
                f"tools_count={len(tool_names)} tool_names={tool_names[:10]}"
            )

        # generate_content is blocking; offload to a thread so the FastAPI
        # event loop stays responsive.
        def _call_sync():
            try:
                return client.models.generate_content(
                    model=settings.gemini_model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=sys_text,
                        temperature=0.4,
                        tools=[tool_decl],
                    ),
                )
            except Exception as exc:
                # Re-raise with a structured request-shape summary appended.
                raise RuntimeError(
                    f"{type(exc).__name__}: {exc} | request_shape: "
                    f"{_shape_summary()}"
                ) from exc

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
