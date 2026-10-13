"""Report-job enqueue (backend side, send-only) — F003.

On session end the backend enqueues a report job to SQS and returns immediately; the dedicated Report
Worker consumes it and scores asynchronously (FR-301). This is the ONLY live-path touch F003 adds — a
single SQS send — so scoring never contends with the interview (Principle I / SC-003).

If REPORT_QUEUE_URL is unset (local dev), enqueue is a logged no-op so the API still works; a report
can then be produced by running the worker one-shot against the session id.
"""

from __future__ import annotations

import json
import logging

from .config import settings

log = logging.getLogger("backend")


def enqueue_report(session_id: str) -> bool:
    """Send one report-job message for `session_id`. Returns True if sent, False if no queue configured
    or the send failed (never raises — enqueue failure must not break ending the session)."""
    queue_url = settings.report_queue_url
    if not queue_url:
        log.info("REPORT_QUEUE_URL unset; skipping enqueue for session %s (run worker one-shot to score)",
                 session_id)
        return False
    try:
        import boto3

        sqs = boto3.client("sqs", region_name=settings.aws_region)
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"session_id": session_id}))
        log.info("enqueued report job for session %s", session_id)
        return True
    except Exception as exc:  # noqa: BLE001 - enqueue failure is non-fatal to ending the session
        log.warning("report enqueue failed for session %s (%s)", session_id, type(exc).__name__)
        return False
