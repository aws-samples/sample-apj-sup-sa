"""Nightly batch invoker for the Biodiversity Anomaly Detection Agent.

EventBridge Scheduler triggers this Lambda on a cron. For each station it reads
the latest month of detections from S3, builds a short "scan for anomalies" prompt,
and invokes the deployed AgentCore Runtime. Any reports the agent generates are
already persisted to the audit bucket by the generate_anomaly_report tool; this
function additionally writes a per-run summary.

This is the "async batch, overnight" latency choice from the product canvas made
real: no human watches the loop, and a report is waiting by morning.

Environment:
    AGENT_RUNTIME_ARN   ARN of the AgentCore Runtime (from `agentcore status`)
    DATA_BUCKET         S3 bucket with the datasets
    AUDIT_BUCKET        S3 bucket for run summaries
    QUALIFIER           Runtime endpoint qualifier (default: DEFAULT)
"""

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import boto3
from botocore.config import Config

s3 = boto3.client("s3")
# Investigations take minutes; raise the read timeout well above the default 60s
# and disable retries so a slow (not failed) call is not abandoned or duplicated.
agentcore = boto3.client(
    "bedrock-agentcore",
    config=Config(read_timeout=840, connect_timeout=10, retries={"max_attempts": 0}),
)

AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
DATA_BUCKET = os.environ["DATA_BUCKET"]
AUDIT_BUCKET = os.environ.get("AUDIT_BUCKET")
QUALIFIER = os.environ.get("QUALIFIER", "DEFAULT")


def _latest_month(detections: list) -> str:
    return max(d["month"] for d in detections)


def _invoke(prompt: str, session_id: str) -> str:
    """Invoke the AgentCore Runtime and return the agent's text result."""
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        qualifier=QUALIFIER,
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
    )
    # Response payload is a streaming body of JSON.
    body = resp["response"].read()
    try:
        return json.loads(body).get("result", body.decode("utf-8"))
    except (ValueError, AttributeError):
        return body.decode("utf-8") if isinstance(body, bytes) else str(body)


def _scan_station(station: dict, month: str, run_id: str) -> dict:
    sid = station["station_id"]
    prompt = (
        f"Nightly anomaly scan for station {sid} ({station.get('name', '')}), "
        f"latest month {month}. Review recent detection trends for all species "
        f"at this station, investigate any species whose counts have dropped "
        f"sharply or disappeared, and generate an anomaly report if warranted. "
        f"If nothing is anomalous, state that no action is needed."
    )
    try:
        # AgentCore requires runtimeSessionId to be 33-128 chars.
        session_id = f"{run_id}-{sid}-{uuid.uuid4().hex}"
        result = _invoke(prompt, session_id=session_id)
        entry = {"station_id": sid, "status": "ok", "result": result}
    except Exception as e:
        entry = {"station_id": sid, "status": "error", "error": str(e)}

    # Persist each station result immediately so progress survives even if the
    # overall run is interrupted.
    if AUDIT_BUCKET:
        s3.put_object(
            Bucket=AUDIT_BUCKET,
            Key=f"batch-runs/{run_id}/{sid}.json",
            Body=json.dumps(entry, indent=2).encode(),
            ContentType="application/json",
        )
    return entry


def handler(event, context):
    detections = json.loads(
        s3.get_object(Bucket=DATA_BUCKET, Key="data/detections.json")["Body"].read()
    )
    stations = detections.get("stations", [])
    month = _latest_month(detections["detections"])
    run_id = f"BATCH-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}"

    # Stations are independent, so investigate them concurrently. This keeps the
    # nightly run within the Lambda timeout instead of summing 6 investigations.
    invocations = []
    with ThreadPoolExecutor(max_workers=len(stations) or 1) as pool:
        futures = {
            pool.submit(_scan_station, st, month, run_id): st["station_id"]
            for st in stations
        }
        for fut in as_completed(futures):
            invocations.append(fut.result())

    summary = {
        "run_id": run_id,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "month_scanned": month,
        "stations_scanned": len(stations),
        "ok": sum(1 for i in invocations if i["status"] == "ok"),
        "errors": sum(1 for i in invocations if i["status"] == "error"),
        "invocations": invocations,
    }
    if AUDIT_BUCKET:
        s3.put_object(
            Bucket=AUDIT_BUCKET,
            Key=f"batch-runs/{run_id}.json",
            Body=json.dumps(summary, indent=2).encode(),
            ContentType="application/json",
        )
    return {
        "run_id": run_id,
        "stations_scanned": len(stations),
        "ok": summary["ok"],
        "errors": summary["errors"],
    }
