"""Public REST API, reachable only through CloudFront (a WAF on the stage checks CloudFront's origin header).

POST /api/generations          start a job: one async worker invocation per model (daily cap)
POST /api/image-generations    Image to 3D: store the upload for GuardDuty to scan (daily upload cap); the
                               prepare function removes the background and starts the models after a clean scan
GET  /api/generations          recent jobs (one shared history; there are no user accounts)
GET  /api/generations/{jobId}  one job, with CloudFront links to its GLB and script
DELETE /api/generations/{jobId} delete the job and all its files (409 while any model is still running)
GET  /api/models               the models compared and their prices
GET  /api/gallery              past generations with one preview GLB each (GALLERY_MODEL_KEYS first); ?nextToken= pages
"""

import base64
import binascii
import json
import logging
import os
import struct
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
DAILY_UPLOAD_LIMIT = int(os.environ.get("DAILY_UPLOAD_LIMIT", "100"))
UPLOADS_BUCKET = os.environ.get("UPLOADS_BUCKET_NAME", "")
OWNER = "public"  # partition of the byOwner index that holds the shared history
# Preferred preview models, in order: GPT-6.2 Astra, then GPT-6 Astra for generations made before the swap.
GALLERY_MODEL_KEYS = [k for k in os.environ.get("GALLERY_MODEL_KEYS", "gpt-6-2-astra,gpt-6-astra").split(",") if k]
GALLERY_PAGE_SIZE = 24
STALE_AFTER_SECONDS = 16 * 60  # the worker's 15-minute timeout plus margin
WORKER_MAX_EVENT_AGE_SECONDS = 9 * 60  # matches the worker's async maxEventAge in the stack
NEVER_STARTED_SECONDS = 10 * 60  # a model still PENDING after this counts as failed (after the event expires)
START_FAILED_MESSAGE = "The model could not be started. Try again."
RETENTION_DAYS = 30
MIN_PROMPT, MAX_PROMPT = 3, 1000
MAX_DESCRIPTION = 300
# Uploads: the page sends a JPEG or PNG downscaled to 1024 px; these limits also suit the background-removal
# model (sides of at least 64 px, at most about 9.4 million pixels, aspect ratio within 1:2.5 and 2.5:1).
MAX_IMAGE_BYTES = 4_000_000
MIN_SIDE, MAX_PIXELS, MAX_ASPECT = 64, 9_437_184, 2.5
INPUT_STALE_SECONDS = 10 * 60  # GuardDuty scan plus background removal; normally well under a minute
IMAGE_JOB_TITLE = "Image to 3D"

_lambda = boto3.client("lambda")
_s3 = boto3.client("s3")


class DailyLimitReached(Exception):
    pass


class BadImage(ValueError):
    pass


def handler(event, _context):
    method = event.get("httpMethod")
    resource = event.get("resource")
    try:
        if resource == "/api/models" and method == "GET":
            return _resp(200, {"models": [_public_slot(m) for m in MODELS]})
        if resource == "/api/generations" and method == "POST":
            return _create(event)
        if resource == "/api/image-generations" and method == "POST":
            return _create_from_image(event)
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


def _json_body(event):
    """The parsed JSON object body, or an error response."""
    content_type = {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get("content-type", "")
    if not content_type.startswith("application/json"):
        return None, _resp(415, {"message": "Content-Type must be application/json"})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return None, _resp(400, {"message": "Body must be JSON"})
    if not isinstance(body, dict):
        return None, _resp(400, {"message": "Body must be a JSON object"})
    return body, None


def _create(event):
    body, error = _json_body(event)
    if error:
        return error
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not MIN_PROMPT <= len(prompt.strip()) <= MAX_PROMPT:
        return _resp(400, {"message": f"prompt must be {MIN_PROMPT}-{MAX_PROMPT} characters"})
    prompt = prompt.strip()
    try:
        selected = _select_models(body.get("models"))
    except ValueError as exc:
        return _resp(400, {"message": str(exc)})

    now = datetime.now(timezone.utc)
    try:
        _count_today(now, "quota", DAILY_LIMIT)
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
        payload = {"jobId": job_id, "modelKey": model["key"], "prompt": prompt}
        if not _start_worker(payload):
            item["models"][model["key"]] = {"status": "FAILED", "error": START_FAILED_MESSAGE}
    log.info("job=%s started models=%s", job_id, ",".join(m["key"] for m in selected))
    return _resp(202, _present(item))


def _start_worker(payload):
    """Start one model's worker; if that fails, record the model as failed so the job doesn't wait on it."""
    try:
        _lambda.invoke(FunctionName=WORKER, InvocationType="Event", Payload=json.dumps(payload).encode())
        return True
    except Exception:  # noqa: BLE001 - one model failing to start must not stop the others
        log.exception("job=%s model=%s could not start the worker", payload["jobId"], payload["modelKey"])
    TABLE.update_item(
        Key={"jobId": payload["jobId"]},
        UpdateExpression="SET #m.#k = :v",
        ExpressionAttributeNames={"#m": "models", "#k": payload["modelKey"]},
        ExpressionAttributeValues={":v": {"status": "FAILED", "error": START_FAILED_MESSAGE}},
    )
    return False


def _create_from_image(event):
    body, error = _json_body(event)
    if error:
        return error
    description = body.get("description") or ""
    if not isinstance(description, str) or len(description.strip()) > MAX_DESCRIPTION:
        return _resp(400, {"message": f"description must be at most {MAX_DESCRIPTION} characters"})
    description = description.strip()
    try:
        data, fmt = _read_image(body.get("image"))
        selected = _select_models(body.get("models"))
    except ValueError as exc:
        return _resp(400, {"message": str(exc)})

    now = datetime.now(timezone.utc)
    try:
        _count_today(now, "uploads", DAILY_UPLOAD_LIMIT)
    except DailyLimitReached:
        return _resp(429, {"message": f"The daily limit of {DAILY_UPLOAD_LIMIT} image uploads is used up. Try again tomorrow (UTC)."})

    job_id = str(uuid.uuid4())
    stamp = now.isoformat(timespec="milliseconds")
    item = {
        "jobId": job_id,
        "owner": OWNER,
        "kind": "image",
        "prompt": description or IMAGE_JOB_TITLE,
        "description": description,
        "createdAt": stamp,
        "expiresAt": int((now + timedelta(days=RETENTION_DAYS)).timestamp()),
        "modelOrder": [m["key"] for m in selected],
        "models": {m["key"]: {"status": "PENDING"} for m in selected},
        "input": {"status": "SCANNING", "updatedAt": stamp},
    }
    TABLE.put_item(Item=item)
    # GuardDuty scans everything written to this bucket and reports to EventBridge, which starts the prepare
    # function. The original is never served: only the background-removed PNG is.
    _s3.put_object(Bucket=UPLOADS_BUCKET, Key=f"uploads/{job_id}/source.{'jpg' if fmt == 'jpeg' else 'png'}",
                   Body=data, ContentType=f"image/{fmt}")
    log.info("job=%s image upload bytes=%d models=%s", job_id, len(data), ",".join(m["key"] for m in selected))
    return _resp(202, _present(item))


def _read_image(value):
    """Decode and check an uploaded image; returns (bytes, "jpeg" | "png")."""
    if not isinstance(value, str) or not value:
        raise BadImage("image must be a base64-encoded JPEG or PNG")
    if value.startswith("data:"):
        value = value.split(",", 1)[-1]
    if len(value) > MAX_IMAGE_BYTES * 4 // 3 + 4:
        raise BadImage("The image is too large (4 MB at most)")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BadImage("image must be valid base64") from exc
    fmt, width, height = _image_size(data)
    if min(width, height) < MIN_SIDE or width * height > MAX_PIXELS or max(width, height) / min(width, height) > MAX_ASPECT:
        raise BadImage(f"The image must be at least {MIN_SIDE} px on each side, at most about 9 megapixels, "
                       f"and no more than {MAX_ASPECT:g} times wider than tall (or taller than wide)")
    return data, fmt


def _image_size(data):
    """(format, width, height) from a PNG or JPEG header, without decoding the image."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return "png", width, height
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # markers without a length
                i += 2
                continue
            (length,) = struct.unpack(">H", data[i + 2:i + 4])
            # Start-of-frame markers carry the size (C4 = Huffman tables, C8 = reserved, CC = arithmetic tables).
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpeg", width, height
            i += 2 + length
    raise BadImage("The image must be a JPEG or PNG file")


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
        if not isinstance(chosen, str):  # an array or object is unhashable and would raise TypeError (a 500)
            raise ValueError(f"The choice for {slot['name']} must be a model key string")
        if chosen not in allowed:
            raise ValueError(f"{chosen!r} is not an option for {slot['name']}")
        selected.append(allowed[chosen])
    return selected


def _count_today(now, counter, limit):
    """Atomically count today's jobs of one kind ("quota" = text prompts, "uploads" = images); refuse past the cap."""
    day = now.strftime("%Y-%m-%d")
    try:
        TABLE.update_item(
            Key={"jobId": f"{counter}#{day}"},
            UpdateExpression="ADD #c :one SET expiresAt = if_not_exists(expiresAt, :exp)",
            ConditionExpression="attribute_not_exists(#c) OR #c < :limit",
            ExpressionAttributeNames={"#c": "count"},
            ExpressionAttributeValues={":one": 1, ":limit": limit, ":exp": int((now + timedelta(days=2)).timestamp())},
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
        "ProjectionExpression": "jobId, #o, prompt, createdAt, modelOrder, #m, kind, #i",
        "ExpressionAttributeNames": {"#o": "owner", "#m": "models", "#i": "input"},
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
        results = _effective_results(item)
        done = [k for k in order if results.get(k, {}).get("status") == "SUCCEEDED" and results[k].get("glbKey")]
        # Preview with the first gallery model that succeeded, otherwise the first model that did.
        key = next((k for k in GALLERY_MODEL_KEYS if k in done), done[0] if done else None)
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
            "kind": item.get("kind", "text"),
            **({"inputImageUrl": f"/{item['input']['imageKey']}"} if (item.get("input") or {}).get("imageKey") else {}),
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
    # A worker that is running, or may still start, would write files after the delete; wait for it to finish.
    if _input_in_progress(item) or any(r.get("status") in ("RUNNING", "PENDING") for r in _effective_results(item).values()):
        return _resp(409, {"message": "This object is still generating. Delete it when every model has finished."})

    prefix = f"generations/{job_id}/"
    deleted = 0
    for page in _s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            _s3.delete_objects(Bucket=BUCKET, Delete={"Objects": keys, "Quiet": True})
            deleted += len(keys)
    if item.get("kind") == "image" and UPLOADS_BUCKET:
        # Normally already gone (the prepare function deletes it, and the bucket expires objects after a day).
        _s3.delete_objects(Bucket=UPLOADS_BUCKET, Delete={"Quiet": True, "Objects": [
            {"Key": f"uploads/{job_id}/source.jpg"}, {"Key": f"uploads/{job_id}/source.png"}]})
    TABLE.delete_item(Key={"jobId": job_id})
    log.info("job=%s deleted files=%d", job_id, deleted)
    return _resp(200, {"jobId": job_id, "deletedFiles": deleted})


def _input_in_progress(item):
    inp = item.get("input") or {}
    return inp.get("status") in ("SCANNING", "REMOVING_BACKGROUND") and _age_seconds(inp.get("updatedAt")) <= INPUT_STALE_SECONDS


def _input_state(item):
    """The image's preparation status; an image that never got through counts as failed after INPUT_STALE_SECONDS."""
    inp = dict(item.get("input") or {})
    if inp.get("status") in ("SCANNING", "REMOVING_BACKGROUND") and not _input_in_progress(item):
        inp["status"], inp["error"] = "FAILED", "Timed out preparing the image"
    return inp


def _effective_results(item):
    """Model results as the page should see them: runs that died, or never started, count as failed."""
    inp = _input_state(item) if item.get("kind") == "image" else None
    # When the workers were started: at creation for text jobs, once the image is ready for Image to 3D.
    queued_at = item.get("createdAt") if inp is None else inp.get("updatedAt") if inp.get("status") == "READY" else None
    out = {}
    for key, raw in (item.get("models") or {}).items():
        r = dict(raw)
        if r.get("status") == "RUNNING" and _age_seconds(r.get("startedAt")) > STALE_AFTER_SECONDS:
            r["status"], r["error"] = "FAILED", "Timed out"
        if inp and inp.get("status") in ("FAILED", "REJECTED") and r.get("status") == "PENDING":
            r["status"], r["error"] = "FAILED", inp.get("error", "The image could not be prepared")
        # Lambda drops a queued worker event after WORKER_MAX_EVENT_AGE_SECONDS, so past that it can't start.
        if r.get("status") == "PENDING" and queued_at and _age_seconds(queued_at) > NEVER_STARTED_SECONDS:
            r["status"], r["error"] = "FAILED", "The model never started"
        out[key] = r
    return out


def _present(item, with_urls=False):
    by_key = MODELS_BY_KEY
    results = []
    effective = _effective_results(item)
    for key in item.get("modelOrder", []):
        r = effective.get(key, {})
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
    out = {"jobId": item["jobId"], "prompt": item["prompt"], "createdAt": item["createdAt"], "results": results,
           "kind": item.get("kind", "text")}
    if item.get("kind") == "image":
        inp = _input_state(item)
        out["input"] = {k: inp[k] for k in ("status", "error", "backgroundSeconds") if k in inp}
        if inp.get("imageKey"):
            out["input"]["imageUrl"] = f"/{inp['imageKey']}"
        out["description"] = item.get("description", "")
    return out


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
