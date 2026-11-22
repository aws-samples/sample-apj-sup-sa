# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Public REST API, reachable only through CloudFront (a WAF on the stage checks CloudFront's origin header).

POST /api/generations          start a job: one async worker invocation per model (daily cap)
GET  /api/generations          recent jobs (one shared history; there are no user accounts)
GET  /api/generations/{jobId}  one job, with CloudFront links to its GLB and script
DELETE /api/generations/{jobId} delete the job and all its files (409 while any model is still running)
GET  /api/models               the models compared and their prices
GET  /api/gallery              past generations with one preview GLB each (GALLERY_MODEL_KEY first); ?nextToken= pages
"""

import base64
import binascii
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
WORKER = os.environ["WORKER_FUNCTION_NAME"]
BUCKET = os.environ["BUCKET_NAME"]
MODELS = json.loads(os.environ["MODELS_JSON"])  # the slots; a slot may list "options" the user can pick instead
RUNNABLE = [m for slot in MODELS for m in [slot, *slot.get("options", [])]]
# Display info for every model a stored job may mention, including retired ones (for example GLM 5).
MODELS_BY_KEY = {m["key"]: m for m in json.loads(os.environ.get("RETIRED_MODELS_JSON", "[]")) + RUNNABLE}
DAILY_LIMIT = int(os.environ.get("DAILY_GENERATION_LIMIT", "100"))
OWNER = "public"  # partition of the byOwner index that holds the shared history
GALLERY_MODEL_KEY = os.environ.get("GALLERY_MODEL_KEY", "gpt-6-astra")
GALLERY_PAGE_SIZE = 24
STALE_AFTER_SECONDS = 16 * 60  # the worker's 15-minute timeout plus margin
RETENTION_DAYS = 30
MIN_PROMPT, MAX_PROMPT = 3, 1000

_lambda = boto3.client("lambda")
_s3 = boto3.client("s3")


class DailyLimitReached(Exception):
    pass


def handler(event, _context):
    method = event.get("httpMethod")
    resource = event.get("resource")
    try:
        if resource == "/api/models" and method == "GET":
            return _resp(200, {"models": [_public_slot(m) for m in MODELS]})
        if resource == "/api/generations" and method == "POST":
            return _create(event)
        if resource == "/api/generations" and method == "GET":
            return _list()
        if resource == "/api/generations/{jobId}" and method == "GET":
            return _get(event["pathParameters"]["jobId"])
        if resource == "/api/generations/{jobId}" and method == "DELETE":
            return _delete(event["pathParameters"]["jobId"])
        if resource == "/api/gallery" and method == "GET":
            return _gallery((event.get("queryStringParameters") or {}).get("nextToken"))
    except Exception:  # noqa: BLE001 - never leak internals to the client
        log.exception("Unhandled error")
        return _resp(500, {"message": "Internal error"})
    return _resp(404, {"message": "Not found"})


def _create(event):
    content_type = {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get("content-type", "")
    if not content_type.startswith("application/json"):
        return _resp(415, {"message": "Content-Type must be application/json"})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"message": "Body must be JSON"})
    prompt = body.get("prompt") if isinstance(body, dict) else None
    if not isinstance(prompt, str) or not MIN_PROMPT <= len(prompt.strip()) <= MAX_PROMPT:
        return _resp(400, {"message": f"prompt must be {MIN_PROMPT}-{MAX_PROMPT} characters"})
    prompt = prompt.strip()
    try:
        selected = _select_models(body.get("models"))
    except ValueError as exc:
        return _resp(400, {"message": str(exc)})

    now = datetime.now(timezone.utc)
    try:
        _count_today(now)
    except DailyLimitReached:
        return _resp(429, {"message": f"The daily limit of {DAILY_LIMIT} generations is used up. Try again tomorrow (UTC)."})

    job_id = str(uuid.uuid4())
    item = {
        "jobId": job_id,
        "owner": OWNER,
        "prompt": prompt,
        "createdAt": now.isoformat(timespec="milliseconds"),
        "expiresAt": int((now + timedelta(days=RETENTION_DAYS)).timestamp()),
        "modelOrder": [m["key"] for m in selected],
        "models": {m["key"]: {"status": "PENDING"} for m in selected},
    }
    TABLE.put_item(Item=item)

    # Fan out: one asynchronous worker per model, so all models generate in parallel.
    for model in selected:
        _lambda.invoke(
            FunctionName=WORKER,
            InvocationType="Event",
            Payload=json.dumps({"jobId": job_id, "modelKey": model["key"], "prompt": prompt}).encode(),
        )
    log.info("job=%s started models=%s", job_id, ",".join(m["key"] for m in selected))
    return _resp(202, _present(item))


def _select_models(choices):
    """One model per slot: the slot's default, or one of its options when the request names it.

    choices maps a slot's default key to the chosen key, e.g. {"claude-opus-5-5": "claude-haiku-5-5"}.
    """
    if choices is None:
        choices = {}
    if not isinstance(choices, dict):
        raise ValueError("models must be an object of slot key to model key")
    slots = {m["key"]: m for m in MODELS}
    unknown = set(choices) - set(slots)
    if unknown:
        raise ValueError(f"Unknown model slot: {sorted(unknown)[0]}")
    selected = []
    for slot in MODELS:
        allowed = {slot["key"]: slot, **{o["key"]: o for o in slot.get("options", [])}}
        chosen = choices.get(slot["key"], slot["key"])
        if chosen not in allowed:
            raise ValueError(f"{chosen!r} is not an option for {slot['name']}")
        selected.append(allowed[chosen])
    return selected


def _count_today(now):
    """Atomically count today's generations; refuse once the cap is reached."""
    day = now.strftime("%Y-%m-%d")
    try:
        TABLE.update_item(
            Key={"jobId": f"quota#{day}"},
            UpdateExpression="ADD #c :one SET expiresAt = if_not_exists(expiresAt, :exp)",
            ConditionExpression="attribute_not_exists(#c) OR #c < :limit",
            ExpressionAttributeNames={"#c": "count"},
            ExpressionAttributeValues={":one": 1, ":limit": DAILY_LIMIT, ":exp": int((now + timedelta(days=2)).timestamp())},
        )
    except TABLE.meta.client.exceptions.ConditionalCheckFailedException as exc:
        raise DailyLimitReached from exc


def _list():
    res = TABLE.query(
        IndexName="byOwner",
        KeyConditionExpression=Key("owner").eq(OWNER),
        ScanIndexForward=False,
        Limit=20,
        ProjectionExpression="jobId, prompt, createdAt",
    )
    return _resp(200, {"generations": res.get("Items", [])})


def _gallery(next_token):
    query = {
        "IndexName": "byOwner",
        "KeyConditionExpression": Key("owner").eq(OWNER),
        "ScanIndexForward": False,
        "Limit": GALLERY_PAGE_SIZE,
        "ProjectionExpression": "jobId, #o, prompt, createdAt, modelOrder, #m",
        "ExpressionAttributeNames": {"#o": "owner", "#m": "models"},
    }
    if next_token:
        start = _decode_token(next_token)
        if start is None:
            return _resp(400, {"message": "Bad nextToken"})
        query["ExclusiveStartKey"] = start
    res = TABLE.query(**query)
    by_key = MODELS_BY_KEY
    items = []
    for item in res.get("Items", []):
        order = item.get("modelOrder", [])
        results = item.get("models", {})
        done = [k for k in order if results.get(k, {}).get("status") == "SUCCEEDED" and results[k].get("glbKey")]
        # Preview with the gallery model when it succeeded, otherwise the first model that did.
        key = GALLERY_MODEL_KEY if GALLERY_MODEL_KEY in done else (done[0] if done else None)
        preview = None
        if key:
            preview = {
                "modelKey": key,
                "modelName": by_key.get(key, {}).get("name", key),
                "glbUrl": f"/{results[key]['glbKey']}",
            }
        items.append({
            "jobId": item["jobId"],
            "prompt": item["prompt"],
            "createdAt": item["createdAt"],
            "succeeded": len(done),
            "total": len(order),
            "finished": sum(1 for k in order if results.get(k, {}).get("status") in ("SUCCEEDED", "FAILED")),
            "preview": preview,
        })
    body = {"items": items}
    if res.get("LastEvaluatedKey"):
        body["nextToken"] = _encode_token(res["LastEvaluatedKey"])
    return _resp(200, body)


def _encode_token(key):
    return base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).decode()


def _decode_token(token):
    """Only accept a byOwner index key for the public partition."""
    try:
        key = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(key, dict) or set(key) != {"jobId", "owner", "createdAt"}:
        return None
    if key["owner"] != OWNER or not all(isinstance(v, str) and len(v) <= 64 for v in key.values()):
        return None
    return key


def _get(job_id):
    try:
        uuid.UUID(job_id)  # also keeps the quota counter items out of reach
    except ValueError:
        return _resp(404, {"message": "Not found"})
    item = TABLE.get_item(Key={"jobId": job_id}).get("Item")
    if not item or item.get("owner") != OWNER:
        return _resp(404, {"message": "Not found"})
    return _resp(200, _present(item, with_urls=True))


def _delete(job_id):
    try:
        uuid.UUID(job_id)
    except ValueError:
        return _resp(404, {"message": "Not found"})
    item = TABLE.get_item(Key={"jobId": job_id}).get("Item")
    if not item or item.get("owner") != OWNER:
        return _resp(404, {"message": "Not found"})
    # A worker that is still running would write files after the delete; wait for it to finish.
    if any(r.get("status") in ("PENDING", "RUNNING") and _age_seconds(r.get("startedAt")) <= STALE_AFTER_SECONDS
           for r in item.get("models", {}).values()):
        return _resp(409, {"message": "This object is still generating. Delete it when every model has finished."})

    prefix = f"generations/{job_id}/"
    deleted = 0
    for page in _s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            _s3.delete_objects(Bucket=BUCKET, Delete={"Objects": keys, "Quiet": True})
            deleted += len(keys)
    TABLE.delete_item(Key={"jobId": job_id})
    log.info("job=%s deleted files=%d", job_id, deleted)
    return _resp(200, {"jobId": job_id, "deletedFiles": deleted})


def _present(item, with_urls=False):
    by_key = MODELS_BY_KEY
    results = []
    for key in item.get("modelOrder", []):
        r = dict(item["models"].get(key, {}))
        if r.get("status") == "RUNNING" and _age_seconds(r.get("startedAt")) > STALE_AFTER_SECONDS:
            r["status"], r["error"] = "FAILED", "Timed out"
        if with_urls and r.get("status") == "SUCCEEDED":
            # Same-origin paths: CloudFront serves /generations/* from the artifacts bucket.
            if r.get("glbKey"):
                r["glbUrl"] = f"/{r['glbKey']}"
            if r.get("scriptKey"):
                r["scriptUrl"] = f"/{r['scriptKey']}"
        r.pop("glbKey", None)
        r.pop("scriptKey", None)
        model = by_key.get(key, {"key": key, "name": key, "provider": "", "modelId": key})
        results.append({"model": _public_model(model), **r})
    return {"jobId": item["jobId"], "prompt": item["prompt"], "createdAt": item["createdAt"], "results": results}


def _public_model(m):
    return {k: m.get(k) for k in ("key", "name", "provider", "modelId", "pricing")}


def _public_slot(slot):
    """A slot's default model; with options, also the full choice list (default first) for the dropdown."""
    out = _public_model(slot)
    if slot.get("options"):
        out["options"] = [_public_model(m) for m in [slot, *slot["options"]]]
    return out


def _age_seconds(iso):
    if not iso:
        return 0
    return time.time() - datetime.fromisoformat(iso).timestamp()


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, default=_json_default),
    }


def _json_default(value):
    # DynamoDB returns numbers as Decimal.
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Cannot serialise {type(value)}")
