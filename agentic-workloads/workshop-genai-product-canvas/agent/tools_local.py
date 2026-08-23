"""Local tool implementations for the Biodiversity Anomaly Detection Agent.

These are @tool-decorated functions that read the same JSON datasets used by the
remote AgentCore Gateway Lambdas. Use these when you want to run the agent fully
locally (no AWS tool backend required) during Part 2 development.

The logic here is intentionally identical to the Lambda handlers in
`infra/lambda/*/index.py` so the agent behaves the same whether it calls local
tools or the remote Gateway. That is the whole point of the workshop: design once,
run anywhere.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from strands import tool

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))


def _load(name: str) -> dict | list:
    return json.loads((DATA_DIR / name).read_text())


@tool
def query_detections(
    species: str, start_month: str, end_month: str, station_id: str = None
) -> dict:
    """Query camera trap detection counts for a species across stations and time.

    Use to establish baselines, identify trends, and confirm a reported anomaly.

    Args:
        species: Species common name (e.g. 'Malayan Tapir').
        start_month: Start month, inclusive, in YYYY-MM format.
        end_month: End month, inclusive, in YYYY-MM format.
        station_id: Optional. Restrict to one station (e.g. 'STN-03').
    """
    data = _load("detections.json")
    records = [
        d
        for d in data["detections"]
        if d["species"] == species
        and start_month <= d["month"] <= end_month
    ]
    if station_id:
        records = [d for d in records if d["station_id"] == station_id]

    records.sort(key=lambda d: d["month"])
    counts = [d["detection_count"] for d in records]
    total = sum(counts)
    mean = round(total / len(counts), 1) if counts else 0

    if len(counts) >= 4:
        # Halves of EQUAL length, compared by mean. Summing counts[: n // 2]
        # against counts[n // 2 :] handed the second bucket an extra month on an
        # odd-length range: seven months of tapir counts ending 4, 3, 1, 0, 0 came
        # out as "stable" because 3 months of early data were weighed against 4
        # months of late data. The middle month is dropped when the count is odd.
        half = len(counts) // 2
        first_half = sum(counts[:half]) / half
        second_half = sum(counts[-half:]) / half
        if second_half < first_half * 0.5:
            trend = "declining"
        elif second_half > first_half * 1.5:
            trend = "increasing"
        else:
            trend = "stable"
    else:
        trend = "insufficient_data"

    return {
        "species": species,
        "station_filter": station_id or "all_stations",
        "records": records,
        "summary": {
            "total_detections": total,
            "mean_monthly": mean,
            "trend": trend,
            "months_covered": len({d["month"] for d in records}),
        },
    }


@tool
def get_weather_data(station_id: str, start_month: str, end_month: str) -> dict:
    """Get monthly weather (rainfall, temperature, humidity, floods) for a station.

    Use to rule out environmental causes such as floods or extreme rainfall.

    Args:
        station_id: Station ID (e.g. 'STN-03').
        start_month: Start month, inclusive, in YYYY-MM format.
        end_month: End month, inclusive, in YYYY-MM format.
    """
    data = _load("weather.json")
    records = [
        w
        for w in data["weather_records"]
        if w["station_id"] == station_id
        and start_month <= w["month"] <= end_month
    ]
    flood_events = sum(1 for r in records if r.get("flood_event"))
    avg_rainfall = (
        round(sum(r["rainfall_mm"] for r in records) / len(records), 1)
        if records
        else 0
    )
    return {
        "station_id": station_id,
        "records": records,
        "flood_events": flood_events,
        "avg_rainfall_mm": avg_rainfall,
    }


@tool
def check_land_use(
    station_id: str, start_date: str, end_date: str, radius_km: float = 5.0
) -> dict:
    """Detect land use changes (logging, construction, agriculture) near a station.

    Use to identify anthropogenic causes of wildlife displacement.

    Args:
        station_id: Station ID (e.g. 'STN-03').
        start_date: Start date, inclusive, in YYYY-MM-DD format.
        end_date: End date, inclusive, in YYYY-MM-DD format.
        radius_km: Search radius in km from the station (default 5).
    """
    data = _load("land_use_changes.json")
    changes = [
        c
        for c in data["changes"]
        if c["station_id"] == station_id
        and start_date <= c["date"] <= end_date
        and c["distance_from_station_km"] <= radius_km
    ]
    total_area = round(sum(c["area_hectares"] for c in changes), 1)
    return {
        "station_id": station_id,
        "radius_km": radius_km,
        "changes": changes,
        "total_area_affected_ha": total_area,
    }


@tool
def search_news(keywords: list, start_date: str, end_date: str) -> dict:
    """Search local news articles by keywords and date range.

    Use to corroborate a hypothesis with external reporting.

    Args:
        keywords: Search terms (e.g. ['logging', 'Sungai Lebam']).
        start_date: Start date, inclusive, in YYYY-MM-DD format.
        end_date: End date, inclusive, in YYYY-MM-DD format.
    """
    data = _load("news_articles.json")
    matches = []
    for article in data["articles"]:
        if not (start_date <= article["date"] <= end_date):
            continue
        haystack = (article["headline"] + " " + article["snippet"]).lower()
        haystack += " " + " ".join(article.get("keywords", [])).lower()
        if any(kw.lower() in haystack for kw in keywords):
            matches.append(article)
    return {"articles": matches, "total_matches": len(matches)}


@tool
def get_species_baseline(species: str) -> dict:
    """Get ecological baseline for a species: IUCN status, ranges, threats, ecology.

    Args:
        species: Species common name (e.g. 'Malayan Tapir').
    """
    data = _load("species_baselines.json")
    match = next((s for s in data if s["species"] == species), None)
    return match if match else {"error": f"Species not found: {species}"}


@tool
def generate_anomaly_report(
    species: str, anomaly_type: str, severity: str, findings: dict
) -> dict:
    """Record the final anomaly investigation report. Call this LAST.

    In the workshop this returns the structured report and, in the deployed
    version, writes it to the audit bucket. There is no mail or ticketing
    integration behind it - wiring one up is the obvious first extension.

    Args:
        species: Affected species name.
        anomaly_type: One of sudden_decline, gradual_decline, disappearance,
            unusual_increase, range_shift.
        severity: One of critical, high, medium, low.
        findings: Object with baseline_range, observed_value, probable_causes[],
            recommended_actions[], escalation_needed.
    """
    report = {
        "report_id": f"RPT-{uuid.uuid4().hex[:8].upper()}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "species": species,
        "anomaly_type": anomaly_type,
        "severity": severity,
        "findings": findings,
        # "recorded", not "published"/"sent": nothing is emailed anywhere, and
        # telling the agent otherwise had it reporting a delivery that never
        # happened. Attach a real notifier and change this to match.
        "status": "recorded",
        "delivery": "none_configured",
    }
    return report


# Convenience list for wiring into a Strands Agent.
LOCAL_TOOLS = [
    query_detections,
    get_weather_data,
    check_land_use,
    search_news,
    get_species_baseline,
    generate_anomaly_report,
]
