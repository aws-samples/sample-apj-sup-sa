# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Run a module on the live deployment and wait for it to finish."""

from __future__ import annotations

import json
import time
from typing import Any

from session import Api

MAX_MISSING_POLLS = 3


def run_module(api: Api, slug: str, body: dict[str, Any] | None = None, timeout: int = 1800) -> dict[str, Any]:
    status, run = api.call("POST", f"/api/modules/{slug}/run", body or {})
    if status != 202:
        return {"status": f"http {status}", "error": json.dumps(run)[:500]}
    started, missing = time.time(), 0
    while time.time() - started < timeout:
        time.sleep(20)
        code, runs = api.call("GET", f"/api/modules/{slug}/runs")
        if code != 200:  # an expired token (ID tokens last an hour) or an outage: say so, not KeyError
            raise RuntimeError(f"listing {slug} runs returned HTTP {code}: {json.dumps(runs)[:300]}")
        current: dict[str, Any] | None = next((r for r in runs["items"] if r["run_id"] == run["run_id"]), None)
        if current is None:
            # Run now lists the run at once; one that is still not listed after a few polls is an API fault,
            # not a run still going, so say that instead of waiting out the timeout (A3-045).
            missing += 1
            if missing >= MAX_MISSING_POLLS:
                return {"status": "not listed", "run_id": run["run_id"]}
            continue
        missing = 0
        if current.get("status") not in ("queued", "running", None):
            return current
    return {"status": "timeout", "run_id": run["run_id"]}
