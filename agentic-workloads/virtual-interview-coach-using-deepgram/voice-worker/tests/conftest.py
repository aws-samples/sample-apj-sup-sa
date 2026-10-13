"""Shared pytest fixtures for the Feature 007 Pipecat processor tests.

Pipecat FrameProcessors that spawn background work (DeadlineProcessor, RecordingProcessor) use
`self.create_task`, which requires a TaskManager that is normally initialized by the pipeline. For
isolated unit tests we provide `init_processor` to wire a minimal TaskManager into a processor so its
create_task/cancel_task work without standing up a full pipeline.
"""

from __future__ import annotations

import asyncio

import pytest

# IMPORTANT: do NOT import pipecat at module top-level. conftest.py is collected for the WHOLE test
# session, so a top-level pipecat import breaks collection of every test (including the pure-Python
# G1 suites) in the rollback venv that has no pipecat installed. Import it lazily inside the fixture
# so only the Pipecat tests that actually USE init_processor require pipecat.


@pytest.fixture
def init_processor():
    """Return an async helper that attaches a live TaskManager to a FrameProcessor (so create_task
    works in tests). The TaskManager is bound to the running loop. pipecat is imported lazily so this
    fixture is only a dependency for the tests that request it."""

    async def _init(proc):
        from pipecat.utils.base_object import BaseObject
        from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams

        tm = TaskManager()
        tm.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
        await BaseObject.setup(proc, tm)
        return proc

    return _init
