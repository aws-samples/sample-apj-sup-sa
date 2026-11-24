import json
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

import boto3


AWS_REGION = os.environ.get("AWS_REGION")
CLAIMS_TABLE_NAME = os.environ.get("CLAIMPILOT_CLAIMS_TABLE_NAME", "")
EVIDENCE_BUCKET_NAME = os.environ.get("CLAIMPILOT_EVIDENCE_BUCKET_NAME", "")
FINAL_PACKET_BUCKET_NAME = (
    os.environ.get("CLAIMPILOT_FINAL_PACKET_BUCKET_NAME") or EVIDENCE_BUCKET_NAME
)
EVIDENCE_ANALYSIS_MODEL = os.environ.get("CLAIMPILOT_EVIDENCE_ANALYSIS_MODEL", "")
API_KEY_SECRET_NAME = os.environ.get("CLAIMPILOT_RUNTIME_API_KEY_SECRET_NAME", "")
PRESIGNED_UPLOAD_EXPIRES_SECONDS = int(
    os.environ.get("CLAIMPILOT_PRESIGNED_UPLOAD_EXPIRES_SECONDS", "900")
)

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
s3 = boto3.client("s3", region_name=AWS_REGION)
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
secretsmanager = boto3.client("secretsmanager", region_name=AWS_REGION)

_api_key_cache = {"value": None, "expires_at": 0}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
        },
        "body": json.dumps(body, default=str),
    }


def get_header(event, name):
    headers = event.get("headers") or {}
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


def get_api_key():
    now = time.time()
    if _api_key_cache["value"] and _api_key_cache["expires_at"] > now:
        return _api_key_cache["value"]

    if not API_KEY_SECRET_NAME:
        return ""

    secret = secretsmanager.get_secret_value(SecretId=API_KEY_SECRET_NAME)
    value = secret.get("SecretString", "")
    _api_key_cache["value"] = value
    _api_key_cache["expires_at"] = now + 300
    return value


def require_auth(event):
    expected = get_api_key()
    provided = get_header(event, "x-claimpilot-api-key")
    return bool(expected and provided and provided == expected)


def normalized_upload_content_type(content_type=""):
    normalized = content_type.split(";")[0].strip().lower()
    if normalized in {"image/jpeg", "image/jpg"}:
        return "image/jpeg"
    if normalized in {"image/png", "image/webp", "image/gif"}:
        return normalized
    return "image/jpeg"


def extension_for_content_type(content_type):
    return {
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(content_type, "jpg")


def safe_s3_name(value):
    safe = "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")
    return safe or "evidence"


def evidence_image_format(s3_key, content_type=""):
    normalized_content_type = content_type.split(";")[0].strip().lower()
    content_type_formats = {
        "image/jpeg": "jpeg",
        "image/jpg": "jpeg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    if normalized_content_type in content_type_formats:
        return content_type_formats[normalized_content_type]

    extension = s3_key.rsplit(".", 1)[-1].lower() if "." in s3_key else ""
    if extension in {"jpg", "jpeg"}:
        return "jpeg"
    if extension in {"png", "gif", "webp"}:
        return extension
    return "jpeg"


def extract_json_object(text):
    decoder = json.JSONDecoder()
    stripped = text.strip()
    for start in (0, stripped.find("{")):
        if start < 0:
            continue
        try:
            value, _ = decoder.raw_decode(stripped[start:])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def clean_string_list(value, fallback=None):
    if not isinstance(value, list):
        return fallback or []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_license_plate(value):
    if not isinstance(value, dict):
        return {"visible": False, "text": None, "confidence": "low"}

    text = value.get("text")
    text = str(text).strip() if text is not None and str(text).strip() else None
    confidence = str(value.get("confidence") or "low").lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    return {
        "visible": bool(value.get("visible") or text),
        "text": text,
        "confidence": confidence,
    }


def normalize_evidence_analysis(raw_analysis):
    visible_damage = clean_string_list(raw_analysis.get("visibleDamage"))
    vehicle_parts = clean_string_list(raw_analysis.get("vehicleParts"))
    license_plate = normalize_license_plate(raw_analysis.get("licensePlate"))
    missing_evidence = clean_string_list(raw_analysis.get("missingEvidence"))
    notes = clean_string_list(raw_analysis.get("notes"))
    adjuster_note = str(raw_analysis.get("adjusterNote") or "").strip()

    finding = str(raw_analysis.get("finding") or "").strip()
    if not finding:
        if visible_damage and vehicle_parts:
            finding = f"{', '.join(vehicle_parts[:2]).title()} damage visible"
        elif visible_damage:
            finding = ", ".join(visible_damage[:2])
        else:
            finding = "Uploaded vehicle evidence analyzed for adjuster review"

    severity = str(raw_analysis.get("severity") or "Unknown").strip().title()
    if severity not in {"None", "Minor", "Moderate", "Severe", "Unknown"}:
        severity = "Unknown"

    if vehicle_parts and not any("part" in note.lower() for note in notes):
        notes.insert(0, f"Visible vehicle part(s): {', '.join(vehicle_parts[:4])}")

    if license_plate["visible"] and license_plate["text"]:
        notes.append(
            f"License plate detected: {license_plate['text']} "
            f"({license_plate['confidence']} confidence)"
        )
    elif license_plate["visible"]:
        notes.append(f"License plate visible but unreadable ({license_plate['confidence']} confidence)")
        missing_evidence.append("Readable license plate")
    else:
        notes.append("License plate not visible in photo")
        missing_evidence.append("License plate not visible")

    if adjuster_note:
        notes.append(adjuster_note)
    notes.extend(["Photo attached to adjuster packet", "Police report number still needed"])

    deduped_notes = []
    for note in notes:
        if note not in deduped_notes:
            deduped_notes.append(note)

    return {
        "finding": finding,
        "severity": severity,
        "notes": deduped_notes,
        "analysisProvider": f"Amazon Bedrock multimodal ({EVIDENCE_ANALYSIS_MODEL})",
        "visibleDamage": visible_damage,
        "vehicleParts": vehicle_parts,
        "licensePlate": license_plate,
        "missingEvidence": list(dict.fromkeys(missing_evidence)),
        "adjusterNote": adjuster_note or "Photo analyzed and attached for adjuster review.",
        "fallbackReason": "",
    }


def bedrock_evidence_prompt(evidence_type):
    return (
        "You are analyzing one uploaded auto insurance claim photo. "
        "Identify only details visible in the image. Do not infer hidden damage. "
        "Focus on three tasks: visible vehicle damage, the damaged car part or parts, "
        "and any license plate text. Return strict JSON only with this schema: "
        '{"finding": string, "severity": "None|Minor|Moderate|Severe|Unknown", '
        '"visibleDamage": string[], "vehicleParts": string[], '
        '"licensePlate": {"visible": boolean, "text": string|null, '
        '"confidence": "high|medium|low"}, "missingEvidence": string[], '
        '"adjusterNote": string, "notes": string[]}. '
        f"The customer evidence type is: {evidence_type}."
    )


def persist_claim_state(payload):
    if not CLAIMS_TABLE_NAME:
        return {"ok": False, "reason": "CLAIMPILOT_CLAIMS_TABLE_NAME not configured"}

    claim_state = payload.get("claimState") or {}
    claim_id = str(payload.get("claimId") or claim_state.get("claimId") or "")
    if not claim_id:
        return {"ok": False, "reason": "claimId is required"}

    status = str(payload.get("status") or claim_state.get("status") or "Draft")
    updated_at = str(payload.get("updatedAt") or utc_now_iso())
    item = {
        "claimId": claim_id,
        "status": status,
        "claimState": claim_state,
        "fields": claim_state.get("fields", {}),
        "missingInformation": claim_state.get("missingInformation", []),
        "evidence": claim_state.get("evidence", []),
        "updatedAt": updated_at,
    }

    for key in (
        "claimType",
        "incidentSummary",
        "safetySummary",
        "adjusterSummary",
        "awsProof",
        "claimSummary",
        "customerSummary",
        "faultAndExcessEstimate",
        "recommendedNextAction",
        "supervisor",
        "timeline",
        "finalPacket",
        "finalPacketStorage",
    ):
        if key in claim_state:
            item[key] = claim_state[key]

    dynamodb.Table(CLAIMS_TABLE_NAME).put_item(Item=item)
    return {"ok": True, "table": CLAIMS_TABLE_NAME}


def create_evidence_upload(payload):
    requested_evidence = str(payload.get("requestedEvidence") or "vehicle damage photo")
    content_type = normalized_upload_content_type(str(payload.get("contentType") or ""))
    claim_id = str(payload.get("claimId") or "draft")
    evidence_id = str(payload.get("evidenceId") or f"ev-{uuid4().hex[:10]}")
    extension = extension_for_content_type(content_type)
    s3_key = str(
        payload.get("s3Key")
        or f"claims/{claim_id}/evidence/{evidence_id}-{safe_s3_name(requested_evidence)}.{extension}"
    )

    if not EVIDENCE_BUCKET_NAME:
        return {
            "ok": False,
            "reason": "CLAIMPILOT_EVIDENCE_BUCKET_NAME not configured",
            "evidenceId": evidence_id,
            "s3Key": s3_key,
            "uploadUrl": None,
            "bucket": None,
            "storage": f"local-demo://{s3_key}",
            "contentType": content_type,
        }

    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": EVIDENCE_BUCKET_NAME,
            "Key": s3_key,
            "ContentType": content_type,
        },
        ExpiresIn=PRESIGNED_UPLOAD_EXPIRES_SECONDS,
    )

    return {
        "ok": True,
        "evidenceId": evidence_id,
        "s3Key": s3_key,
        "uploadUrl": upload_url,
        "bucket": EVIDENCE_BUCKET_NAME,
        "storage": f"s3://{EVIDENCE_BUCKET_NAME}/{s3_key}",
        "contentType": content_type,
    }


def analyze_evidence(payload):
    if not EVIDENCE_ANALYSIS_MODEL:
        return {"ok": False, "reason": "CLAIMPILOT_EVIDENCE_ANALYSIS_MODEL not configured"}

    evidence = payload.get("evidence") or {}
    evidence_type = str(payload.get("evidenceType") or "vehicle damage photo")
    bucket = str(evidence.get("bucket") or EVIDENCE_BUCKET_NAME)
    s3_key = str(evidence.get("s3Key") or payload.get("s3Key") or "")
    if not bucket or not s3_key:
        return {"ok": False, "reason": "bucket and s3Key are required"}

    image_response = s3.get_object(Bucket=bucket, Key=s3_key)
    image_bytes = image_response["Body"].read()
    image_format = evidence_image_format(s3_key, str(image_response.get("ContentType", "")))

    bedrock_response = bedrock.converse(
        modelId=EVIDENCE_ANALYSIS_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"text": bedrock_evidence_prompt(evidence_type)},
                    {
                        "image": {
                            "format": image_format,
                            "source": {"bytes": image_bytes},
                        }
                    },
                ],
            }
        ],
        inferenceConfig={"maxTokens": 700, "temperature": 0},
    )
    content_blocks = bedrock_response.get("output", {}).get("message", {}).get("content", [])
    response_text = "\n".join(
        str(block.get("text", "")) for block in content_blocks if block.get("text")
    )
    parsed = extract_json_object(response_text)
    if parsed is None:
        return {
            "ok": False,
            "reason": f"Bedrock vision returned non-JSON response: {response_text[:300]}",
        }

    analysis = normalize_evidence_analysis(parsed)
    return {
        "ok": True,
        "analysis": analysis,
        "storage": f"s3://{bucket}/{s3_key}",
        "model": EVIDENCE_ANALYSIS_MODEL,
    }


def store_final_packet(payload):
    if not FINAL_PACKET_BUCKET_NAME:
        return {"ok": False, "reason": "CLAIMPILOT_FINAL_PACKET_BUCKET_NAME not configured"}

    final_packet = payload.get("finalPacket") or {}
    claim_id = str(final_packet.get("claimId") or payload.get("claimId") or "")
    if not claim_id:
        return {"ok": False, "reason": "claimId is required"}

    s3_key = str(payload.get("s3Key") or f"claims/{claim_id}/final-packet.json")
    s3.put_object(
        Bucket=FINAL_PACKET_BUCKET_NAME,
        Key=s3_key,
        Body=json.dumps(final_packet, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    return {
        "ok": True,
        "bucket": FINAL_PACKET_BUCKET_NAME,
        "s3Key": s3_key,
        "storage": f"s3://{FINAL_PACKET_BUCKET_NAME}/{s3_key}",
    }


ACTIONS = {
    "persistClaimState": persist_claim_state,
    "createEvidenceUpload": create_evidence_upload,
    "analyzeEvidence": analyze_evidence,
    "storeFinalPacket": store_final_packet,
}


def handler(event, _context):
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return response(204, {})

    if not require_auth(event):
        return response(401, {"ok": False, "reason": "Unauthorized"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"ok": False, "reason": "Invalid JSON body"})

    action = body.get("action")
    payload = body.get("payload") or {}
    if action not in ACTIONS:
        return response(400, {"ok": False, "reason": f"Unknown action: {action}"})

    try:
        return response(200, ACTIONS[action](payload))
    except Exception as exc:
        print(f"ClaimPilot runtime action failed action={action}: {exc}")
        return response(500, {"ok": False, "reason": str(exc)})
