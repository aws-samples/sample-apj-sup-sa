"""Report Worker entrypoint — SQS consume loop (R1/R6).

Long-polls the report queue; for each message containing a session_id, claims the report_job (guarded
transition — idempotent against at-least-once delivery and concurrent workers), generates the report,
and deletes the message on success. On failure the job is marked failed and the message is left for SQS
redrive. Runs entirely off the live path (consumed after the interview ends).

Can also be invoked one-shot for a single session (no SQS) — used by the harness / manual runs:
    python -m src.consume --session <session_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

from .config import Config
from . import guidance, persistence, retention

log = logging.getLogger("report_worker")

_RECEIVE_BACKOFF_S = 5  # pause after a transient SQS receive error before retrying


def _delete_message(sqs, queue_url: str, receipt_handle: str, *, session_id: str) -> None:
    """Delete a processed/malformed SQS message. A delete failure is logged but never propagated —
    it is independent of the scoring outcome (a redelivery is idempotent via the report_job claim
    guard), so it must not be mislabeled as a scoring failure (code-review finding #4)."""
    try:
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    except Exception as exc:  # noqa: BLE001 - delete is best-effort; redelivery is harmless (idempotent)
        log.warning("SQS delete failed for session %s (%s); message will redeliver (idempotent)",
                    session_id, type(exc).__name__)


async def _maybe_refresh_guidance(session_id: str, config: Config) -> None:
    """F008 (US4): after a session is SCORED, regenerate its owner's cross-session coaching
    guidance (one Bedrock call over their report-derived history; batch-by-construction — it
    rides the scoring queue). Best-effort and contained exactly like the retention sweep: a
    failure is logged and never affects the scoring outcome or the consume loop; the previous
    guidance row stays (the dashboard keeps showing it with its generated_at — FR-012)."""
    try:
        conn = await persistence.connect(config)
        try:
            user_sub = await persistence.user_sub_for_session(conn, session_id)
            if user_sub:
                await guidance.refresh_guidance(conn, user_sub, config)
        finally:
            await conn.close()
    except Exception as exc:  # noqa: BLE001 - guidance must never break the consume loop
        log.warning("guidance refresh failed (%s); previous guidance retained", type(exc).__name__)


async def _maybe_sweep_retention(config: Config) -> None:
    """Run the F006 retention TTL sweep (off the live path). Best-effort: a failure is logged and
    never interrupts SQS consumption."""
    if config.retention_sweep_interval_s <= 0:
        return
    try:
        conn = await persistence.connect(config)
        try:
            await retention.sweep_expired(config, conn)
        finally:
            await conn.close()
    except Exception as exc:  # noqa: BLE001 - retention must never break the consume loop
        log.warning("retention sweep failed (%s); will retry next interval", type(exc).__name__)


def _setup_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=handlers)


async def process_session(session_id: str, config: Config, *, score_fn=None, feedback_fn=None) -> str:
    """Claim + generate a report for one session. Returns the final status."""
    persistence.set_active_config(config)
    conn = await persistence.connect(config)
    try:
        won = await persistence.claim_job(conn, session_id)
        if not won:
            log.info("session %s already processing/scored; skipping (idempotent)", session_id)
            return "skipped"
        try:
            status = await persistence.generate_report(conn, session_id, config,
                                                        score_fn=score_fn, feedback_fn=feedback_fn)
            await persistence.finish_job(conn, session_id, status)
            return status
        except Exception as exc:  # noqa: BLE001 - mark failed, never leave 'processing' stuck
            log.warning("session %s report failed (%s)", session_id, type(exc).__name__)
            await persistence.finish_job(conn, session_id, "failed", error=type(exc).__name__)
            raise
    finally:
        await conn.close()


async def consume_loop(config: Config) -> None:
    """Long-poll SQS forever, processing one session per message."""
    import boto3

    if not config.sqs_queue_url:
        raise RuntimeError("REPORT_QUEUE_URL is not configured")
    sqs = boto3.client("sqs", region_name=config.aws_region)
    log.info("report worker consuming from %s", config.sqs_queue_url)
    next_sweep = 0.0  # run the retention sweep on the first iteration, then every interval
    while True:
        # F006 retention TTL sweep, off the live path, between SQS long-polls.
        if config.retention_sweep_interval_s > 0 and time.monotonic() >= next_sweep:
            await _maybe_sweep_retention(config)
            next_sweep = time.monotonic() + config.retention_sweep_interval_s
        # A transient SQS error (throttle / network / expired creds) must NOT crash the consume loop
        # and halt all report processing — back off briefly and retry (code-review finding #4).
        try:
            resp = sqs.receive_message(
                QueueUrl=config.sqs_queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=config.sqs_wait_seconds,
                VisibilityTimeout=config.sqs_visibility_timeout,
            )
        except Exception as exc:  # noqa: BLE001 - keep the loop alive across transient SQS failures
            log.warning("SQS receive failed (%s); backing off %ds", type(exc).__name__, _RECEIVE_BACKOFF_S)
            await asyncio.sleep(_RECEIVE_BACKOFF_S)
            continue
        for msg in resp.get("Messages", []):
            session_id = _session_id_from_body(msg.get("Body", ""))
            if not session_id:
                log.warning("dropping malformed message (no session_id)")
                _delete_message(sqs, config.sqs_queue_url, msg["ReceiptHandle"], session_id="<malformed>")
                continue
            try:
                status = await process_session(session_id, config)
            except Exception:  # noqa: BLE001 - scoring failed; leave message for redrive (job marked failed)
                log.warning("session %s scoring failed; left on queue for redrive", session_id)
                continue
            # Scoring SUCCEEDED — delete the message. A delete failure here is NOT a scoring failure:
            # do not mislog it as "left on queue"; the claim guard makes a redelivery idempotent.
            _delete_message(sqs, config.sqs_queue_url, msg["ReceiptHandle"], session_id=session_id)
            # F008: a freshly SCORED session refreshes its owner's coaching guidance (contained).
            if status == "scored":
                await _maybe_refresh_guidance(session_id, config)


def _session_id_from_body(body: str) -> str | None:
    try:
        data = json.loads(body)
        return data.get("session_id")
    except Exception:  # noqa: BLE001 - allow a bare session id as the body too
        return body.strip() or None


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Report worker: SQS consume loop or one-shot session.")
    parser.add_argument("--session", help="score a single session id and exit (no SQS)")
    args = parser.parse_args()
    config = Config.load()
    _setup_logging(config.log_file)
    if args.session:
        status = await process_session(args.session, config)
        log.info("one-shot session %s -> %s", args.session, status)
    else:
        await consume_loop(config)


if __name__ == "__main__":
    asyncio.run(_main())
