"""POST /parent/actions/{action_id}/revert — chat-only Undo endpoint.

Used exclusively by the chat agent's ReceiptBubble. Profile UI direct
buttons and the shield/block dispatcher do NOT call this. The
dispatcher inverts a small set of action_types corresponding to the
agent's tool registry."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from backend.app.services.bigkid_store import BigKidStore, get_store as get_bigkid_store
from backend.app.services.parent_action_log import (
    ActionLogEntry, ParentActionLog, get_log as get_action_log,
)


router = APIRouter(tags=["Parent Actions"])


@router.post("/parent/actions/{action_id}/revert")
def revert_action(
    action_id: str,
    log: ParentActionLog = Depends(get_action_log),
    store: BigKidStore = Depends(get_bigkid_store),
) -> dict:
    entry = log.get(action_id)
    if entry is None or entry.reverted:
        raise HTTPException(status_code=410, detail="Action expired or already reverted")

    new_undo_token = _execute_inverse(entry, store, log)
    log.mark_reverted(action_id)
    return {"reverted_action_id": action_id, "new_undo_token": new_undo_token}


# ---------- Inverse dispatcher ----------
# v1 supports inverses for the small set of agent tools that have one:
# - approve_task ↔ request_redo  (each is the other's inverse)
# - propose_reflection → cancel_reflection  (one-way; cancel has no inverse)
# - respond_bypass → respond_bypass (decision flipped)
# Anything else = no-op revert (entry was emitted with inverse_action=None).

def _execute_inverse(
    entry: ActionLogEntry, store: BigKidStore, log: ParentActionLog
) -> str | None:
    inv_action = entry.inverse_action
    inv_args = entry.inverse_args
    if inv_action is None:
        return None

    if inv_action == "request_redo":
        store.parent_review_task(
            child_id=UUID(inv_args["child_id"]),
            task_id=UUID(inv_args["task_id"]),
            decision="redo",
            redo_reason=inv_args.get("redo_reason"),
        )
        return log.record(
            action_type="request_redo", args=inv_args,
            inverse_action="approve_task",
            inverse_args={"child_id": inv_args["child_id"], "task_id": inv_args["task_id"]},
            source="agent",
        )

    if inv_action == "approve_task":
        store.parent_review_task(
            child_id=UUID(inv_args["child_id"]),
            task_id=UUID(inv_args["task_id"]),
            decision="approve",
            redo_reason=None,
        )
        return log.record(
            action_type="approve_task", args=inv_args,
            inverse_action="request_redo",
            inverse_args={
                "child_id": inv_args["child_id"], "task_id": inv_args["task_id"],
                "redo_reason": "Reverted",
            },
            source="agent",
        )

    if inv_action == "cancel_reflection":
        # Use the public ack_reflection method — it clears the reflection
        # cleanly and resets the cooldown. Reaching into _states is a
        # leaky abstraction we avoid.
        cid = UUID(inv_args["child_id"])
        rid_str = inv_args.get("rid")
        if rid_str:
            try:
                store.ack_reflection(cid, UUID(rid_str))
            except Exception as exc:
                logger.warning("cancel_reflection revert noop: {}", exc)
        return None  # cancel has no inverse

    if inv_action == "respond_bypass":
        store.respond_bypass(
            bypass_id=UUID(inv_args["bypass_id"]),
            decision=inv_args["decision"],
            message=inv_args.get("message"),
        )
        flipped = "deny" if inv_args["decision"] == "approve" else "approve"
        return log.record(
            action_type="respond_bypass", args=inv_args,
            inverse_action="respond_bypass",
            inverse_args={
                "bypass_id": inv_args["bypass_id"],
                "decision": flipped,
                "message": "Reverted",
            },
            source="agent",
        )

    logger.warning("revert: unknown inverse_action {}", inv_action)
    return None
