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
    modules in a single test doesn't double-register."""
    try:
        from backend.app.services.agent_tools import GLOBAL_REGISTRY
    except ImportError:
        yield
        return
    snapshot = dict(GLOBAL_REGISTRY.tools)
    yield
    GLOBAL_REGISTRY.tools.clear()
    GLOBAL_REGISTRY.tools.update(snapshot)
