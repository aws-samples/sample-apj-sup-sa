# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The order in which a module run executes the tool calls of one model response.

Strands runs every call of a response at once by default. Two things go wrong with that in a module run:

* a write in the same response as the fetch it cites is checked by the output guard before the fetch has
  recorded what it read, so the citation is defanged and the evidence stamped ``fetched=false``
  (P3-066). Writes therefore run after the response's other calls, one at a time;
* the run's ``SafeFetcher`` holds four connections, and a fifth concurrent ``http_fetch`` waits five
  seconds for one and then fails as a pool timeout, which reads to the model as a fetch error (A3-028).
  The other calls therefore run at most ``MAX_PARALLEL`` at a time.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from strands.tools.executors import ConcurrentToolExecutor
from strands.types._events import TypedEvent
from strands.types.tools import ToolResult, ToolUse

MAX_PARALLEL = 4  # insights_radar.core.fetch.SafeFetcher's pool size


class WritesLastExecutor(ConcurrentToolExecutor):
    def __init__(self, writes: frozenset[str], max_parallel: int = MAX_PARALLEL) -> None:
        super().__init__()
        self._writes = writes
        self._max_parallel = max_parallel

    def batches(self, tool_uses: list[ToolUse]) -> list[list[ToolUse]]:
        """Reads in groups of at most ``max_parallel``, then each write on its own, in the model's order."""
        reads = [u for u in tool_uses if str(u.get("name", "")) not in self._writes]
        writes = [u for u in tool_uses if str(u.get("name", "")) in self._writes]
        step = self._max_parallel
        return [reads[i : i + step] for i in range(0, len(reads), step)] + [[w] for w in writes]

    async def _execute(
        self,
        agent: Any,
        tool_uses: list[ToolUse],
        tool_results: list[ToolResult],
        cycle_trace: Any,
        cycle_span: Any,
        invocation_state: dict[str, Any],
        structured_output_context: Any = None,
    ) -> AsyncGenerator[TypedEvent, None]:
        for batch in self.batches(tool_uses):
            events = super()._execute(
                agent, batch, tool_results, cycle_trace, cycle_span, invocation_state, structured_output_context
            )
            async for event in events:
                yield event
