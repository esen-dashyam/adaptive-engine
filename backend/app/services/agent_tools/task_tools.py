"""Task-domain tools — assign / delete / approve / redo."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from backend.app.schemas.bigkid import TaskCategory
from backend.app.services.agent_tools.decorator import tool, ToolResult, GLOBAL_REGISTRY
from backend.app.services.bigkid_store import get_store as get_bigkid_store


@tool(
    name="assign_task",
    description=(
        "Create a new task on the kid's list. category must be one of: "
        "'Chores', 'Homework', 'Self-care'. due is human-readable like "
        "'Today, 6:00 PM' (optional)."
    ),
    requires_confirm=False,
    danger="low",
    inverse_action="delete_task",
    label_builder=lambda args: f"Assign task: {args.get('title', '')}",
    registry=GLOBAL_REGISTRY,
)
async def assign_task(
    child_id: UUID, title: str, description: str, category: str,
    due: Optional[str] = None,
) -> ToolResult:
    cat = TaskCategory(category)
    task = get_bigkid_store().create_task(
        child_id, title=title.strip(), description=description.strip(),
        category=cat, due=due,
    )
    return ToolResult(
        public={"task_id": str(task.id), "title": task.title},
        public_summary=f"Assigned '{task.title}'",
    )


@tool(
    name="delete_task",
    description="Remove a task from the kid's list.",
    requires_confirm=True,
    danger="medium",
    inverse_action=None,
    label_builder=lambda args: f"Delete task {args.get('task_id', '')[:8]}",
    registry=GLOBAL_REGISTRY,
)
async def delete_task(child_id: UUID, task_id: UUID) -> ToolResult:
    get_bigkid_store().delete_task(child_id, task_id)
    return ToolResult(
        public={"task_id": str(task_id), "deleted": True},
        public_summary=f"Deleted task {str(task_id)[:8]}",
    )


@tool(
    name="approve_task",
    description=(
        "Mark a kid's submitted task as completed. The kid sees the green "
        "'Approved!' screen immediately and the time pool may unlock more "
        "screen time. Reversible (Undo flips it to redo)."
    ),
    requires_confirm=True,
    danger="medium",
    inverse_action="request_redo",
    inverse_args_builder=lambda args, _r: {
        "child_id": args["child_id"], "task_id": args["task_id"],
        "redo_reason": "Reverted",
    },
    label_builder=lambda args: f"Approve task {args.get('task_id', '')[:8]}",
    registry=GLOBAL_REGISTRY,
)
async def approve_task(
    child_id: UUID, task_id: UUID, *, authorize_batch: bool = False,
) -> ToolResult:
    task = get_bigkid_store().parent_review_task(
        child_id=child_id, task_id=task_id, decision="approve", redo_reason=None,
    )
    return ToolResult(
        public={"task_id": str(task.id), "status": task.status.value},
        public_summary=f"Approved '{task.title}'",
    )


@tool(
    name="request_redo",
    description=(
        "Send a submitted task back to the kid for redo. The kid sees the "
        "amber redo banner with your reason. Reversible (Undo approves it)."
    ),
    requires_confirm=True,
    danger="medium",
    inverse_action="approve_task",
    inverse_args_builder=lambda args, _r: {
        "child_id": args["child_id"], "task_id": args["task_id"],
    },
    label_builder=lambda args: f"Redo task {args.get('task_id', '')[:8]}",
    registry=GLOBAL_REGISTRY,
)
async def request_redo(
    child_id: UUID, task_id: UUID, redo_reason: str,
    *, authorize_batch: bool = False,
) -> ToolResult:
    task = get_bigkid_store().parent_review_task(
        child_id=child_id, task_id=task_id, decision="redo", redo_reason=redo_reason,
    )
    return ToolResult(
        public={"task_id": str(task.id), "status": task.status.value},
        public_summary=f"Sent '{task.title}' back for redo",
    )
