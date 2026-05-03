"""Big-kid parent endpoints used by the parent app + tests.
Full Gemini-driven reflection content is wired in Phase 9; this scaffold
uses fixture content so the v1 child flow is testable end-to-end.
"""
from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from backend.app.core.settings import settings
from pydantic import BaseModel

from backend.app.schemas.bigkid import (
    BypassRequest, ChildStateResponse, ParentBypassRespondBody,
    ParentReflectionApproveBody, ParentReflectionTriggerBody,
    ParentTaskReviewBody, QuizQuestionPublic, ReflectionRequest, Task,
    TaskCategory,
)
from backend.app.services.bigkid_store import BigKidStore, get_store
from backend.app.services.gemini_reflection import generate_reflection_content


router = APIRouter(tags=["Big-Kid Parent"])


@router.post("/parent/_debug/reset/{child_id}", response_model=ChildStateResponse)
def debug_reset_state(
    child_id: UUID,
    store: BigKidStore = Depends(get_store),
) -> ChildStateResponse:
    """DEBUG: drop a child's state from the store so the next /child/state
    or /parent/state call re-seeds from fixture (Make bed / Math homework /
    Brush teeth, all back to todo). Used by the kid app's debug menu so
    you can re-test the flow without redeploying / re-pairing.

    Note: evidence photos in Supabase Storage stay (they're keyed by
    UUID). New tasks have new UUIDs so they don't reference old uploads."""
    store._states.pop(child_id, None)  # noqa: SLF001
    return store.get_state(child_id)


@router.get("/parent/_supabase_debug")
async def supabase_debug() -> dict:
    """Diagnose Supabase connectivity from inside Railway. Reports the
    URL/key shape (without leaking secrets) and tries a real DNS lookup +
    HTTP HEAD against the configured URL so we can tell whether the
    failure is `not configured`, `typo`, `project paused`, or something
    else."""
    import socket
    from urllib.parse import urlparse
    import httpx

    url = settings.supabase_url
    key = settings.supabase_key
    out: dict = {
        "url_set": bool(url),
        "url_length": len(url),
        # First 12 + last 12 chars only — enough to spot typos without
        # exposing the full host.
        "url_preview": (url[:12] + "..." + url[-12:]) if len(url) > 24 else url,
        "url_has_whitespace": url != url.strip(),
        "url_has_quotes": url.startswith('"') or url.startswith("'"),
        "key_set": bool(key),
        "key_length": len(key),
    }
    if not url:
        out["verdict"] = "SUPABASE_URL not visible to settings"
        return out

    parsed = urlparse(url.strip())
    host = parsed.hostname or ""
    out["parsed_host"] = host
    out["parsed_scheme"] = parsed.scheme

    try:
        ip = socket.gethostbyname(host)
        out["dns_ok"] = True
        out["dns_resolves_to"] = ip
    except Exception as exc:
        out["dns_ok"] = False
        out["dns_error"] = repr(exc)
        out["verdict"] = (
            "Hostname doesn't resolve. Either typo in URL, project paused, "
            "or Railway egress can't reach Supabase DNS."
        )
        return out

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{parsed.scheme}://{host}/rest/v1/",
                            headers={"apikey": key, "Authorization": f"Bearer {key}"})
        out["http_status"] = r.status_code
        out["verdict"] = "OK" if r.status_code < 500 else f"Supabase returned {r.status_code}"
    except Exception as exc:
        out["http_error"] = repr(exc)
        out["verdict"] = "DNS resolved but HTTP request failed"
    return out


@router.get("/parent/_bigkid_debug")
async def bigkid_debug() -> dict:
    """Diagnose why a trigger fell through to fixture. Returns whether
    Gemini key is visible and, if so, attempts a real Gemini call so any
    auth / model / parse error bubbles up instead of being swallowed by
    the trigger endpoint's `except`."""
    has_key = bool(settings.gemini_api_key)
    no_gemini_flag = os.environ.get("BIGKID_NO_GEMINI", "0") == "1"
    if not has_key:
        return {"has_key": False, "no_gemini_flag": no_gemini_flag,
                "verdict": "GEMINI_API_KEY not visible to settings — set it on Railway env"}
    if no_gemini_flag:
        return {"has_key": True, "no_gemini_flag": True,
                "verdict": "BIGKID_NO_GEMINI=1 is forcing fixture"}
    try:
        content = await generate_reflection_content(reason="debug test sentence")
        return {"has_key": True, "no_gemini_flag": False, "ok": True,
                "display_reason_sample": content.display_reason,
                "first_quiz_q": content.quiz[0].q if content.quiz else None}
    except Exception as e:
        return {"has_key": True, "no_gemini_flag": False, "ok": False,
                "error": repr(e)}


@router.post("/parent/reflection/trigger", response_model=ReflectionRequest)
async def trigger_reflection(
    body: ParentReflectionTriggerBody,
    store: BigKidStore = Depends(get_store),
) -> ReflectionRequest:
    # Gemini-driven content path. Auto-enabled whenever the project's
    # `settings.gemini_api_key` is configured (matches every other
    # Gemini call site in the codebase — supports both real env vars
    # and a `.env` file picked up by pydantic-settings).
    # `BIGKID_NO_GEMINI=1` forces the fixture path (offline / unit tests).
    use_gemini = (
        bool(settings.gemini_api_key)
        and os.environ.get("BIGKID_NO_GEMINI", "0") != "1"
    )
    if use_gemini:
        try:
            content = await generate_reflection_content(reason=body.reason)
            return store.trigger_reflection_with_content(
                body.child_id, reason=body.reason,
                display_reason=content.display_reason,
                video_id=content.video_id, video_title=content.video_title,
                writing_prompt=content.writing_prompt,
                quiz_public=[QuizQuestionPublic(q=q.q, options=q.options) for q in content.quiz],
                correct_indices=[q.correct_index for q in content.quiz],
            )
        except Exception as e:
            # Fall through to fixture so the kid flow still works, but
            # surface the reason in logs so you can diagnose.
            print(f"[bigkid_parent] Gemini path failed, using fixture: {e!r}")
    return store.trigger_reflection(body.child_id, body.reason)


@router.post("/parent/reflection/{rid}/approve", response_model=ReflectionRequest)
def approve_reflection(
    rid: UUID, body: ParentReflectionApproveBody,
    store: BigKidStore = Depends(get_store),
) -> ReflectionRequest:
    # NOTE: child_id derivation from rid is left to the real auth implementation.
    # For v1 in-memory store we scan all states.
    for cid, s in store._states.items():  # noqa: SLF001
        if s.reflection and s.reflection.id == rid:
            return store.parent_approve_reflection(cid, rid, parent_note=body.parent_note)
    raise HTTPException(status_code=404, detail="reflection not found")


@router.post("/parent/task/{task_id}/review", response_model=Task)
def review_task(
    task_id: UUID, body: ParentTaskReviewBody,
    store: BigKidStore = Depends(get_store),
) -> Task:
    for cid, s in store._states.items():  # noqa: SLF001
        for t in s.tasks:
            if t.id == task_id:
                return store.parent_review_task(
                    cid, task_id, decision=body.decision, redo_reason=body.redo_reason,
                )
    raise HTTPException(status_code=404, detail="task not found")


# ---------- parent reads kid state ----------
# Mirrors /child/state but lives under /parent so the parent app doesn't have
# to call kid-scoped endpoints. Parent passes the same child_id stored in
# @AppStorage("evlin.childDeviceID") (set during pairing).

@router.get("/parent/state/{child_id}", response_model=ChildStateResponse)
def parent_state(
    child_id: UUID,
    store: BigKidStore = Depends(get_store),
) -> ChildStateResponse:
    return store.get_state(child_id)


# ---------- parent creates a task on the child ----------

class ParentTaskCreateBody(BaseModel):
    child_id: UUID
    title: str
    description: str
    category: TaskCategory
    due: str | None = None  # human-readable, e.g. "Today, 6:00 PM"


@router.post("/parent/task", response_model=Task)
def create_task(
    body: ParentTaskCreateBody,
    store: BigKidStore = Depends(get_store),
) -> Task:
    return store.create_task(
        body.child_id,
        title=body.title.strip(),
        description=body.description.strip(),
        category=body.category,
        due=(body.due or None),
    )


# ---------- parent deletes a task ----------
# NOTE: status_code is set at runtime (Response return) rather than on the
# decorator. FastAPI 0.111.x asserts at register time that 204 endpoints
# cannot declare a response body — passing status_code=204 in the decorator
# trips that assertion and crashes the whole app at boot. Returning a
# Response object dodges the check while still emitting the right status.

@router.delete("/parent/task/{task_id}", response_model=None)
def delete_task(
    task_id: UUID,
    store: BigKidStore = Depends(get_store),
) -> Response:
    for cid, s in store._states.items():  # noqa: SLF001
        for t in s.tasks:
            if t.id == task_id:
                store.delete_task(cid, task_id)
                return Response(status_code=status.HTTP_204_NO_CONTENT)
    raise HTTPException(status_code=404, detail="task not found")


# ---------- parent responds to a bypass request ----------

@router.post("/parent/bypass/{bypass_id}/respond", response_model=BypassRequest)
def respond_bypass(
    bypass_id: UUID,
    body: ParentBypassRespondBody,
    store: BigKidStore = Depends(get_store),
) -> BypassRequest:
    try:
        return store.respond_bypass(
            bypass_id, decision=body.decision, message=body.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
