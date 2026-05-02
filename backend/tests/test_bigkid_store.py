"""Tests for in-memory big-kid store."""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from backend.app.services.bigkid_store import BigKidStore
from backend.app.schemas.bigkid import (
    BypassStatus, ReflectionStatus, ReflectionStep, TaskStatus,
)


CHILD = UUID("11111111-1111-1111-1111-111111111111")


def test_seed_creates_three_tasks_and_no_reflection() -> None:
    store = BigKidStore()
    state = store.get_state(CHILD)
    assert state.child_name == "Liam"
    assert state.minutes_max == 120
    assert state.minutes_left == 0  # tasks not done → no time pool yet
    assert len(state.tasks) == 3
    assert state.reflection_request is None
    assert state.daily_complete_acknowledged is False


def test_submit_evidence_flips_task_to_submitted_and_withdraws_pending_bypass() -> None:
    store = BigKidStore()
    state = store.get_state(CHILD)
    task = state.tasks[0]
    bypass = store.create_bypass(CHILD, task.id, "I forgot my notebook")
    assert bypass.status == BypassStatus.pending
    store.submit_evidence(CHILD, task.id, photo_url="https://example.test/p.jpg", note=None)
    refreshed = store.get_state(CHILD)
    refreshed_task = next(t for t in refreshed.tasks if t.id == task.id)
    assert refreshed_task.status == TaskStatus.submitted
    assert refreshed_task.bypass is not None
    assert refreshed_task.bypass.status == BypassStatus.withdrawn


def test_parent_approves_task_marks_done_and_unlocks_pool_when_all_done() -> None:
    store = BigKidStore()
    state = store.get_state(CHILD)
    for t in state.tasks:
        store.submit_evidence(CHILD, t.id, photo_url="https://example.test/p.jpg", note=None)
        store.parent_review_task(CHILD, t.id, decision="approve", redo_reason=None)
    refreshed = store.get_state(CHILD)
    assert all(t.status == TaskStatus.done for t in refreshed.tasks)
    assert refreshed.minutes_left == 120


def test_reflection_lifecycle_pending_to_submitted_to_approved() -> None:
    store = BigKidStore()
    store.trigger_reflection(CHILD, reason="kept scrolling")
    state = store.get_state(CHILD)
    assert state.reflection_request is not None
    rid = state.reflection_request.id
    assert state.reflection_request.status == ReflectionStatus.pending
    # video done
    store.complete_reflection_step(CHILD, rid, ReflectionStep.video)
    # quiz: answer all correctly
    for i in range(5):
        store.answer_quiz_question(CHILD, rid, question_index=i, selected_index=_correct_index(store, CHILD, rid, i))
    # writing
    store.submit_essay(CHILD, rid, text="I was tired and bored. I will read instead. Sorry.")
    after_submit = store.get_state(CHILD).reflection_request
    assert after_submit is not None
    assert after_submit.status == ReflectionStatus.submitted
    # parent approves
    store.parent_approve_reflection(CHILD, rid, parent_note="thanks for being honest")
    after_approve = store.get_state(CHILD).reflection_request
    assert after_approve is not None
    assert after_approve.status == ReflectionStatus.approved
    # ack drops it
    store.ack_reflection(CHILD, rid)
    assert store.get_state(CHILD).reflection_request is None


def _correct_index(store: BigKidStore, child: UUID, rid: UUID, q_idx: int) -> int:
    return store._reflection_correct_indices(child, rid)[q_idx]
