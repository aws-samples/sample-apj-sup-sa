"""Worker entry point (ECS task): discovery agent -> benchmark -> load results into Aurora."""

from __future__ import annotations

import json
import logging
import os
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bedrock_bench import __main__ as cli
from bedrock_bench.auth import AuthBroker
from bedrock_bench.registry import save_registry

from . import db
from .agent import run_discovery

logger = logging.getLogger("worker")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    broker = AuthBroker()
    if os.environ.get("SKIP_DISCOVERY") != "1":
        try:
            logger.info("discovery report:\n%s", run_discovery(broker, os.environ["AGENT_MODEL_ID"]))
        except Exception:  # noqa: BLE001 - benchmark the existing registry even if discovery fails
            logger.exception("discovery failed; continuing with the stored registry")

    specs = db.load_registry()
    if not specs:
        logger.error("registry is empty; nothing to benchmark")
        return 1
    work = Path(tempfile.mkdtemp(prefix="bench-"))
    registry = work / "models.json"
    save_registry(specs, registry, datetime.now(timezone.utc).isoformat(timespec="seconds"))

    args = shlex.split(os.environ.get("BENCHMARK_ARGS", "--preset quick"))
    out_dir = work / "results"
    rc = cli.main([*args, "--registry", str(registry), "--output-dir", str(out_dir), "--public"])
    summaries = sorted(out_dir.glob("*/summary.json"))
    if not summaries:
        logger.error("benchmark produced no summary (exit %s)", rc)
        return rc or 1
    cells = db.load_summary(json.loads(summaries[-1].read_text()))
    logger.info("loaded %d cells from %s", cells, summaries[-1].parent.name)
    return rc


if __name__ == "__main__":
    sys.exit(main())
