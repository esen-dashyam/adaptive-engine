"""Shared pytest fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_in_memory_state():
    """Wipe process-local singletons between tests so order is irrelevant."""
    from backend.app.services import bigkid_store
    bigkid_store._singleton = None  # noqa: SLF001
    try:
        from backend.app.services import parent_action_log
        parent_action_log._singleton = None  # noqa: SLF001
    except ImportError:
        pass
    try:
        from backend.app.services import proposal_store
        proposal_store._singleton = None  # noqa: SLF001
    except ImportError:
        pass
    yield


@pytest.fixture(autouse=True)
def _isolate_global_tool_registry():
    """Snapshot+restore GLOBAL_REGISTRY around each test so tools added
    by one test don't leak into another, and so re-importing tool
    modules in a single test doesn't double-register.

    Note: Python caches module imports. So if a test imports a tool
    module that registered into GLOBAL_REGISTRY on first import, a
    subsequent test won't re-trigger registration. To keep tests
    independent, we eagerly import all known tool modules here so the
    snapshot captures them once Python has loaded any of them."""
    try:
        from backend.app.services.agent_tools import GLOBAL_REGISTRY
    except ImportError:
        yield
        return
    # Best-effort eager import of all tool modules so the snapshot is
    # populated even if individual tests don't import them.
    for mod in (
        "read_tools", "vision_tools", "task_tools",
        "reflection_tools", "bypass_tools", "lock_tools",
    ):
        try:
            __import__(f"backend.app.services.agent_tools.{mod}")
        except ImportError:
            pass
    snapshot = dict(GLOBAL_REGISTRY.tools)
    yield
    GLOBAL_REGISTRY.tools.clear()
    GLOBAL_REGISTRY.tools.update(snapshot)
