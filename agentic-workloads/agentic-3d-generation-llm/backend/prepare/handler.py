"""Prepare an Image to 3D upload once GuardDuty has scanned it, then start the models.

Triggered by EventBridge with GuardDuty's "Malware Protection Object Scan Result" for the uploads bucket.
- Threat found, or the scan was skipped: the job is rejected and the upload deleted.
- Clean: Bedrock removes the background, the PNG is saved as generations/<jobId>/input.png (served
  through CloudFront), the original upload is deleted, and one worker per model is started.
"""

import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.config import Config

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
UPLOADS_BUCKET = os.environ["UPLOADS_BUCKET_NAME"]
BUCKET = os.environ["BUCKET_NAME"]
WORKER = os.environ["WORKER_FUNCTION_NAME"]
BG_MODEL_ID = os.environ.get("BACKGROUND_MODEL_ID", "us.stability.stable-image-remove-background-v1:0")
UPLOAD_KEY = re.compile(r"^uploads/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/source\.(jpg|png)$")

_s3 = boto3.client("s3")
_lambda = boto3.client("lambda")
_bedrock = boto3.client("bedrock-runtime", config=Config(read_timeout=90, retries={"mode": "adaptive", "max_attempts": 4}))


class Rejected(Exception):
    """The upload can't be used; the message is shown to the user."""


def handler(event, _context):
    detail = event.get("detail") or {}
    obj = detail.get("s3ObjectDetails") or {}
    key = obj.get("objectKey", "")
    match = UPLOAD_KEY.match(key)
    if obj.get("bucketName") != UPLOADS_BUCKET or not match:
        log.warning("Ignoring scan result for %s/%s", obj.get("bucketName"), key)
        return
    job_id = match.group(1)
    item = TABLE.get_item(Key={"jobId": job_id}).get("Item")
    if not item or (item.get("input") or {}).get("status") != "SCANNING":
        log.info("job=%s not waiting for a scan (duplicate event or deleted job)", job_id)
        return

    result = (detail.get("scanResultDetails") or {}).get("scanResultStatus")
    try:
        if detail.get("scanStatus") != "COMPLETED" or result != "NO_THREATS_FOUND":
            reasons = (detail.get("scanResultDetails") or {}).get("statusReasons") or []
            log.warning("job=%s upload rejected: scan=%s result=%s reasons=%s", job_id, detail.get("scanStatus"), result, reasons)
            raise Rejected("The upload was rejected by the malware scan." if result == "THREATS_FOUND"
                           else "The upload couldn't be scanned for malware, so it wasn't used.")
        if not _claim(job_id):
            return  # another delivery of this event got here first
        started = time.monotonic()
        png = _remove_background(_s3.get_object(Bucket=UPLOADS_BUCKET, Key=key)["Body"].read())
        image_key = f"generations/{job_id}/input.png"
        _s3.put_object(Bucket=BUCKET, Key=image_key, Body=png, ContentType="image/png")
        seconds = time.monotonic() - started
    except Rejected as exc:
        _fail(job_id, item, "REJECTED", str(exc))
        _delete_upload(key)
        return
    except Exception:  # noqa: BLE001 - record any failure on the job
        log.exception("job=%s background removal failed", job_id)
        _fail(job_id, item, "FAILED", "Background removal failed. Try another image.")
        _delete_upload(key)
        return

    _delete_upload(key)  # only the background-removed PNG is kept
    TABLE.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET #i = :i",
        ExpressionAttributeNames={"#i": "input"},
        ExpressionAttributeValues={":i": {
            "status": "READY", "imageKey": image_key, "updatedAt": _now(),
            "backgroundSeconds": Decimal(str(round(seconds, 2))), "imageBytes": len(png),
        }},
    )
    for model_key in item.get("modelOrder", []):
        _start_worker({"jobId": job_id, "modelKey": model_key, "prompt": item.get("description", ""), "imageKey": image_key})
    log.info("job=%s image ready bytes=%d seconds=%.1f; started %d models", job_id, len(png), seconds, len(item.get("modelOrder", [])))


def _start_worker(payload):
    """Start one model's worker; if that fails, record the model as failed so the job doesn't wait on it."""
    try:
        _lambda.invoke(FunctionName=WORKER, InvocationType="Event", Payload=json.dumps(payload).encode())
        return
    except Exception:  # noqa: BLE001 - one model failing to start must not stop the others
        log.exception("job=%s model=%s could not start the worker", payload["jobId"], payload["modelKey"])
    TABLE.update_item(
        Key={"jobId": payload["jobId"]},
        UpdateExpression="SET #m.#k = :v",
        ExpressionAttributeNames={"#m": "models", "#k": payload["modelKey"]},
        ExpressionAttributeValues={":v": {"status": "FAILED", "error": "The model could not be started. Try again."}},
    )


def _claim(job_id):
    """Move the job from SCANNING to REMOVING_BACKGROUND, once."""
    try:
        TABLE.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #i.#s = :next, #i.updatedAt = :now",
            ConditionExpression="#i.#s = :scanning",
            ExpressionAttributeNames={"#i": "input", "#s": "status"},
            ExpressionAttributeValues={":next": "REMOVING_BACKGROUND", ":scanning": "SCANNING", ":now": _now()},
        )
        return True
    except TABLE.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def _remove_background(image: bytes) -> bytes:
    resp = _bedrock.invoke_model(
        modelId=BG_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"image": base64.b64encode(image).decode(), "output_format": "png"}),
    )
    body = json.loads(resp["body"].read())
    reasons = [r for r in body.get("finish_reasons") or [] if r]
    if reasons:
        log.warning("Background removal filtered: %s", reasons)
        raise Rejected("The image was blocked by the background-removal model's content filter.")
    images = body.get("images") or []
    if not images:
        raise RuntimeError("No image in the background-removal response")
    png = base64.b64decode(images[0])
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Background removal did not return a PNG")
    return png


def _fail(job_id, item, status, message):
    """Mark the input and every model as failed, so the page stops waiting."""
    names = {"#i": "input"}
    values = {":i": {"status": status, "error": message, "updatedAt": _now()}}
    sets = ["#i = :i"]
    for n, model_key in enumerate(item.get("modelOrder", [])):
        names[f"#k{n}"] = model_key
        values[f":m{n}"] = {"status": "FAILED", "error": message}
        sets.append(f"#m.#k{n} = :m{n}")
    if len(sets) > 1:
        names["#m"] = "models"
    TABLE.update_item(Key={"jobId": job_id}, UpdateExpression="SET " + ", ".join(sets),
                      ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def _delete_upload(key):
    try:
        _s3.delete_object(Bucket=UPLOADS_BUCKET, Key=key)
    except Exception:  # noqa: BLE001 - the bucket's 1-day lifecycle rule removes it anyway
        log.exception("Could not delete %s", key)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
