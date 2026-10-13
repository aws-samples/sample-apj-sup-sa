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
            # The full report is stored in discovery_events (Aurora); logs get its size only.
            report = run_discovery(broker, os.environ["AGENT_MODEL_ID"])
            logger.info("discovery finished (report: %d characters, stored in discovery_events)", len(report))
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
    if os.environ.get("BATCH_BY_MODEL") == "1":
        return _run_batched(args, registry, work, [s.key for s in specs])
    out_dir = work / "results"
    rc = cli.main([*args, "--registry", str(registry), "--output-dir", str(out_dir), "--public"])
    summaries = sorted(out_dir.glob("*/summary.json"))
    if not summaries:
        logger.error("benchmark produced no summary (exit %s)", rc)
        return rc or 1
    cells = db.load_summary(json.loads(summaries[-1].read_text()))
    logger.info("loaded %d cells from %s", cells, summaries[-1].parent.name)
    return rc


def _run_batched(args: list[str], registry: Path, work: Path, keys: list[str]) -> int:
    """One benchmark per model, each loaded into the same run as it finishes.

    For multi-day presets: a crash or stop keeps every finished model, and the run is marked
    finished (shown as the latest run) only after the last model is in.
    """
    started = datetime.now(timezone.utc)
    run_id = f"run-{started:%Y%m%d-%H%M%S}-periodic"
    loaded, failed = 0, []
    for i, key in enumerate(keys):
        out_dir = work / f"results-{i:03d}"
        rc = cli.main([*args, "--keys", key, "--registry", str(registry), "--output-dir", str(out_dir), "--public"])
        summaries = sorted(out_dir.glob("*/summary.json"))
        if not summaries:
            logger.error("model %s produced no summary (exit %s)", key, rc)
            failed.append(key)
            continue
        cells = db.load_summary(
            json.loads(summaries[-1].read_text()),
            run_id=run_id,
            started=started.isoformat(timespec="seconds"),
            finish=i == len(keys) - 1,
        )
        loaded += cells
        logger.info("model %d/%d %s: loaded %d cells into %s", i + 1, len(keys), key, cells, run_id)
    if failed and keys[-1] in failed:
        # The last model carries the finish mark; set it anyway so the finished models are shown.
        db.execute(
            "UPDATE runs SET finished = now() WHERE run_id = :run_id AND finished IS NULL", {"run_id": run_id}
        )
    logger.info("periodic run %s: %d cells, %d models failed %s", run_id, loaded, len(failed), failed)
    return 0 if loaded else 1

if __name__ == "__main__":
    sys.exit(main())
