# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Worker: one invocation per (job, model). Calls the AgentCore runtime, stores the files, records metrics."""

import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.config import Config

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
BUCKET = os.environ["BUCKET_NAME"]
RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
# Every model the API may start: each slot's default and its dropdown options.
MODELS = {m["key"]: m for slot in json.loads(os.environ["MODELS_JSON"]) for m in [slot, *slot.get("options", [])]}

_s3 = boto3.client("s3")
_agentcore = boto3.client(
    "bedrock-agentcore",
    config=Config(read_timeout=870, connect_timeout=10, retries={"max_attempts": 1}),
)


RETRY_MIN_REMAINING_SECONDS = 300  # only retry a cold-start failure if this much Lambda time is left
LAMBDA_MARGIN_SECONDS = 60  # time kept back to store files and record the result


def handler(event, context):
    job_id, key, prompt = event["jobId"], event["modelKey"], event["prompt"]
    model = MODELS[key]
    started_at = _now()
    _update(job_id, key, {"status": "RUNNING", "startedAt": started_at})
    started = time.monotonic()
    try:
        try:
            result = _invoke_agent(job_id, model, prompt)
        except _agentcore.exceptions.RuntimeClientError as exc:
            remaining = context.get_remaining_time_in_millis() / 1000 if context else 0
            if not _is_cold_start_timeout(exc) or remaining < RETRY_MIN_REMAINING_SECONDS:
                raise
            # A new microVM occasionally misses AgentCore's 120 s start-up limit; try once more in a fresh session.
            log.warning("job=%s model=%s cold start timed out; retrying with %.0fs left", job_id, key, remaining)
            result = _invoke_agent(job_id, model, prompt, attempt=2, deadline=int(remaining - LAMBDA_MARGIN_SECONDS))
    except Exception as exc:  # noqa: BLE001 - record any failure on the job
        log.exception("job=%s model=%s agent invocation failed", job_id, key)
        _update(job_id, key, {
            "status": "FAILED",
            "startedAt": started_at,
            "error": f"Agent invocation failed: {type(exc).__name__}",
            "finishedAt": _now(),
            "durationSeconds": _dec(time.monotonic() - started),
        })
        return

    usage = result.get("usage") or {}
    timings = result.get("timings") or {}
    record = {
        "status": result.get("status", "FAILED"),
        "startedAt": started_at,
        "finishedAt": _now(),
        "durationSeconds": _dec(time.monotonic() - started),
        "llmSeconds": _dec(timings.get("llmSeconds", 0)),
        "blenderSeconds": _dec(timings.get("blenderSeconds", 0)),
        "inputTokens": int(usage.get("inputTokens", 0)),
        "outputTokens": int(usage.get("outputTokens", 0)),
        "cacheReadTokens": int(usage.get("cacheReadTokens", 0)),
        "totalTokens": int(usage.get("totalTokens", 0)),
        "llmCalls": int(usage.get("llmCalls", 0)),
        "attempts": len(result.get("attempts") or []),
        "costUsd": _dec(_cost(model, usage), 6),
    }
    prefix = f"generations/{job_id}/{key}"
    if result.get("script"):
        record["scriptKey"] = f"{prefix}/script.py"
        _s3.put_object(Bucket=BUCKET, Key=record["scriptKey"], Body=result["script"].encode(), ContentType="text/plain; charset=utf-8")
    if record["status"] == "SUCCEEDED":
        record["glbKey"] = f"{prefix}/model.glb"
        record["glbBytes"] = int(result.get("glbBytes", 0))
        mesh = _mesh_metrics(result.get("meshMetrics"))
        if mesh:
            record["mesh"] = mesh
        _s3.put_object(
            Bucket=BUCKET, Key=record["glbKey"], Body=base64.b64decode(result["glbBase64"]), ContentType="model/gltf-binary"
        )
    else:
        record["error"] = str(result.get("error") or "Generation failed")[:1000]
    _update(job_id, key, record)
    log.info("job=%s model=%s status=%s tokens=%s cost=%s", job_id, key, record["status"], record["totalTokens"], record["costUsd"])


def _invoke_agent(job_id, model, prompt, attempt=1, deadline=None):
    payload = {"prompt": prompt, "api": model["api"], "modelId": model["modelId"]}
    if model.get("requestFields"):
        payload["requestFields"] = model["requestFields"]  # model-specific Converse fields, e.g. reasoning effort
    if model.get("maxOutputTokens"):
        payload["maxOutputTokens"] = model["maxOutputTokens"]  # a model's own output cap
    if model.get("mantlePath"):
        payload["mantlePath"] = model["mantlePath"]  # e.g. Gemma 4 is under /openai/v1 on bedrock-mantle
    if deadline:
        payload["deadlineSeconds"] = deadline
    session = f"{job_id}-{model['key']}" + (f"-r{attempt}" if attempt > 1 else "")
    resp = _agentcore.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        qualifier="DEFAULT",
        runtimeSessionId=session,  # one isolated session (microVM) per model
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode(),
    )
    body = resp["response"].read()
    return json.loads(body)


MESH_COUNTS = (
    "objects", "pieces", "vertices", "faces", "triangles", "holes", "boundaryEdges", "nonManifoldEdges",
    "flippedFaces", "looseVertices", "looseEdges", "zeroAreaFaces", "duplicateVertices",
)


def _mesh_metrics(raw):
    """Mesh-quality counts from the agent, kept to known fields (ints and booleans) before storing."""
    if not isinstance(raw, dict):
        return None
    out = {k: int(raw[k]) for k in MESH_COUNTS if isinstance(raw.get(k), int) and not isinstance(raw.get(k), bool)}
    if not out:
        return None
    out["watertight"] = raw.get("watertight") is True
    if raw.get("partial") is True:
        out["partial"] = True
    return out


def _is_cold_start_timeout(exc):
    return "initialization time exceeded" in str(exc).lower()


def _cost(model, usage):
    p = model["pricing"]
    cache_read_price = p.get("cacheReadPerMTok", p["inputPerMTok"] * 0.1)
    return (
        usage.get("inputTokens", 0) * p["inputPerMTok"]
        + usage.get("outputTokens", 0) * p["outputPerMTok"]
        + usage.get("cacheReadTokens", 0) * cache_read_price
        + usage.get("cacheWriteTokens", 0) * p.get("cacheWritePerMTok", p["inputPerMTok"] * 1.25)
    ) / 1_000_000


def _update(job_id, key, fields):
    names = {"#m": "models", "#k": key}
    TABLE.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET #m.#k = :v",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues={":v": fields},
    )


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _dec(value, places=2):
    return Decimal(str(round(float(value), places)))
