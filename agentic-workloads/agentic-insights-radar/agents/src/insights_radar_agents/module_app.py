# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AgentCore entry point of the module runtime (``python -m insights_radar_agents.module_app``)."""

from __future__ import annotations

import logging
import threading
from typing import Any

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from insights_radar_agents.connectors import Connectors
from insights_radar_agents.models import ModelFactory
from insights_radar_agents.module_runtime import ModuleRunner, TaskToken, TaskTokenReporter
from insights_radar_agents.settings import RuntimeSettings, configure_logging
from insights_radar_agents.tool_api import SigV4HttpxAuth, ToolApi, credentials_of

configure_logging()
log = logging.getLogger("air.module")


class ModuleApp:
    def __init__(self, settings: RuntimeSettings, session: Any = None, sfn: Any = None) -> None:
        self.settings = settings
        self._session = session or boto3.Session(region_name=settings.region)
        credentials = credentials_of(self._session)  # refreshable: read on every signature
        # The tool gate is the AgentTools function URL (signed for "lambda"); older stacks served it on
        # the HTTP API.
        tools_url = settings.agent_tools_url or settings.api_url
        service = "lambda" if settings.agent_tools_url else "execute-api"
        self._api_auth = SigV4HttpxAuth(credentials, settings.region, service)
        gateway_auth = SigV4HttpxAuth(credentials, settings.region, "bedrock-agentcore")
        self.runner = ModuleRunner(
            api_factory=lambda token: ToolApi(tools_url, token, self._api_auth),
            models=ModelFactory(settings.region, settings.guardrail),
            connectors=Connectors(settings.region, settings.gateway_url, gateway_auth),
        )
        self._sfn = sfn or self._session.client("stepfunctions")
        self._lock = threading.Lock()
        self._active: dict[str, TaskToken] = {}
        self.app = BedrockAgentCoreApp()
        self.app.entrypoint(self.invoke)

    def invoke(self, payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
        """Start a run on a worker thread. A second invoke for a run that is still running (a Step Functions
        retry of the invoke, with a new task token and the same session) starts no second runner: the
        running one reports to the new token instead (P3-089)."""
        if not isinstance(payload, dict) or not all(payload.get(k) for k in ("run_token", "task_token", "run_id")):
            return {"accepted": False, "error": "run_token, task_token and run_id are required"}
        run_id = str(payload["run_id"])[:64]
        with self._lock:
            running = self._active.get(run_id)
            if running is not None:
                running.replace(str(payload["task_token"]))
                log.warning("run %s is already running: it reports to the new task token", run_id)
                return {"accepted": True, "run_id": run_id, "already_running": True}
            token = TaskToken(str(payload["task_token"]))
            self._active[run_id] = token
        task_id = self.app.add_async_task("module_run", {"run_id": run_id})

        def work() -> None:
            try:
                TaskTokenReporter(self._sfn).run(token, lambda: self.runner.run(payload))
            finally:
                with self._lock:
                    self._active.pop(run_id, None)
                self.app.complete_async_task(task_id)

        threading.Thread(target=work, daemon=True, name=f"run-{run_id}").start()
        log.info("accepted run %s for module %s", run_id, (payload.get("module") or {}).get("slug"))
        return {"accepted": True, "run_id": run_id}


def main() -> None:  # pragma: no cover - process entry point
    ModuleApp(RuntimeSettings.from_env()).app.run()


if __name__ == "__main__":  # pragma: no cover
    main()
