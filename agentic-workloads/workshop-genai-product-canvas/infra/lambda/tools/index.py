"""AgentCore Gateway Lambda target: the six wildlife-investigation tools.

Amazon Bedrock AgentCore Gateway turns this single Lambda into a remote MCP
server. When the agent calls a tool, Gateway invokes this function with:

  * event   = the tool's input arguments (the JSON `properties`)
  * context.client_context.custom['bedrockAgentCoreToolName'] = the tool name,
              e.g. "wildlife-investigation-tools___query_detections"

We strip the target prefix and dispatch to the matching function. The logic is
identical to agent/tools_local.py so behaviour matches whether the agent runs
against local tools or these remote ones.

Datasets are read from S3 (bucket in DATA_BUCKET env var, keyed under data/).
Every call is written to the audit bucket for compliance.
"""

import json
import os
import traceback
import uuid
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")

DATA_BUCKET = os.environ["DATA_BUCKET"]
AUDIT_BUCKET = os.environ.get("AUDIT_BUCKET")
_CACHE: dict[str, object] = {}


def _load(name: str):
    if name not in _CACHE:
        obj = s3.get_object(Bucket=DATA_BUCKET, Key=f"data/{name}")
        _CACHE[name] = json.loads(obj["Body"].read())
    return _CACHE[name]


# --- Tool implementations --------------------------------------------------

def query_detections(species, start_month, end_month, station_id=None):
    data = _load("detections.json")
    records = [
        d for d in data["detections"]
        if d["species"] == species and start_month <= d["month"] <= end_month
    ]
    if station_id:
        records = [d for d in records if d["station_id"] == station_id]
    records.sort(key=lambda d: d["month"])
    counts = [d["detection_count"] for d in records]
    total = sum(counts)
    mean = round(total / len(counts), 1) if counts else 0
    if len(counts) >= 4:
        # Compare the mean of the first half against the mean of the last half,
        # over halves of equal length. Summing counts[:n//2] against counts[n//2:]
        # gave the second bucket an extra month on an odd-length range, which
        # reported a species that had dropped to zero as "stable".
        h = len(counts) // 2
        first = sum(counts[:h]) / h
        second = sum(counts[-h:]) / h
        trend = "declining" if second < first * 0.5 else "increasing" if second > first * 1.5 else "stable"
    else:
        trend = "insufficient_data"
    return {
        "species": species,
        "station_filter": station_id or "all_stations",
        "records": records,
        "summary": {"total_detections": total, "mean_monthly": mean, "trend": trend,
                     "months_covered": len({d["month"] for d in records})},
    }


def get_weather_data(station_id, start_month, end_month):
    data = _load("weather.json")
    records = [
        w for w in data["weather_records"]
        if w["station_id"] == station_id and start_month <= w["month"] <= end_month
    ]
    floods = sum(1 for r in records if r.get("flood_event"))
    avg = round(sum(r["rainfall_mm"] for r in records) / len(records), 1) if records else 0
    return {"station_id": station_id, "records": records,
            "flood_events": floods, "avg_rainfall_mm": avg}


def check_land_use(station_id, start_date, end_date, radius_km=5.0):
    data = _load("land_use_changes.json")
    changes = [
        c for c in data["changes"]
        if c["station_id"] == station_id and start_date <= c["date"] <= end_date
        and c["distance_from_station_km"] <= radius_km
    ]
    return {"station_id": station_id, "radius_km": radius_km, "changes": changes,
            "total_area_affected_ha": round(sum(c["area_hectares"] for c in changes), 1)}


def search_news(keywords, start_date, end_date):
    data = _load("news_articles.json")
    matches = []
    for a in data["articles"]:
        if not (start_date <= a["date"] <= end_date):
            continue
        hay = (a["headline"] + " " + a["snippet"] + " " + " ".join(a.get("keywords", []))).lower()
        if any(kw.lower() in hay for kw in keywords):
            matches.append(a)
    return {"articles": matches, "total_matches": len(matches)}


def get_species_baseline(species):
    data = _load("species_baselines.json")
    return next((s for s in data if s["species"] == species), {"error": f"Species not found: {species}"})


def generate_anomaly_report(species, anomaly_type, severity, findings):
    report = {
        "report_id": f"RPT-{uuid.uuid4().hex[:8].upper()}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "species": species, "anomaly_type": anomaly_type, "severity": severity,
        # The report is written to the audit bucket below and nowhere else. It used
        # to claim "published" / "sent_to_ecologist_inbox", which had the agent
        # telling the user about an email that no code ever sends.
        "findings": findings, "status": "recorded", "delivery": "audit_bucket",
    }
    if AUDIT_BUCKET:
        s3.put_object(
            Bucket=AUDIT_BUCKET,
            Key=f"reports/{report['report_id']}.json",
            Body=json.dumps(report, indent=2).encode(),
            ContentType="application/json",
        )
    return report


DISPATCH = {
    "query_detections": query_detections,
    "get_weather_data": get_weather_data,
    "check_land_use": check_land_use,
    "search_news": search_news,
    "get_species_baseline": get_species_baseline,
    "generate_anomaly_report": generate_anomaly_report,
}


# --- Handler ---------------------------------------------------------------

def _tool_name_from_context(context) -> str:
    raw = ""
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    raw = custom.get("bedrockAgentCoreToolName", "")
    # Gateway prefixes with the target name: "<target>___<tool>"
    return raw.split("___")[-1] if "___" in raw else raw


def _audit(tool_name: str, args: dict, ok: bool):
    if not AUDIT_BUCKET:
        return
    ts = datetime.now(timezone.utc)
    entry = {"timestamp": ts.isoformat(), "tool": tool_name, "arguments": args, "success": ok}
    try:
        s3.put_object(
            Bucket=AUDIT_BUCKET,
            Key=f"audit/{ts:%Y/%m/%d}/{tool_name}-{uuid.uuid4().hex[:8]}.json",
            Body=json.dumps(entry).encode(),
            ContentType="application/json",
        )
    except Exception as exc:  # noqa: BLE001 - auditing must never break a tool call
        # Swallowed on purpose, but not silently: this module promises that every
        # call is audited, so a gap in the audit trail has to be visible somewhere.
        print(f"AUDIT WRITE FAILED for {tool_name}: {exc}")


def handler(event, context):
    tool_name = _tool_name_from_context(context)
    args = event if isinstance(event, dict) else {}
    fn = DISPATCH.get(tool_name)
    if fn is None:
        _audit(tool_name or "unknown", args, False)
        return {"error": f"Unknown tool: {tool_name}", "known_tools": list(DISPATCH)}
    try:
        result = fn(**args)
        _audit(tool_name, args, True)
        return result
    except TypeError as e:
        _audit(tool_name, args, False)
        return {"error": f"Invalid arguments for {tool_name}: {e}"}
    except Exception as e:  # noqa: BLE001
        # Anything else is a real fault. Audit it as a failure and log the
        # traceback - catching only TypeError above meant a genuine error left no
        # audit record at all, which is the one thing the audit trail must not do.
        _audit(tool_name, args, False)
        traceback.print_exc()
        return {"error": f"{tool_name} failed: {type(e).__name__}: {e}"}
