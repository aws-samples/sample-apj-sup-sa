"""Estimate the actual AWS bill for a Batch submission.

Cost model
----------
We attribute cost to **unique EC2 instances** (not individual Batch
jobs), because AWS bills you for the full wall-clock lifespan of each
instance — including provisioning, image pull, idle time between jobs,
and drain. Summing ``stoppedAt - startedAt`` across jobs would miss
20-40% of the actual bill.

For each unique EC2 instance:
1. Resolve instance metadata (type, AZ, launch time, termination time,
   lifecycle) via :mod:`llm_batch_deploy.waiter.instance_resolver`.
2. Fetch the hourly rate:
   * Spot instances → ``DescribeSpotPriceHistory`` for the specific AZ +
     time window. Handles mid-lifespan price changes via integral.
   * On-demand / unresolved → AWS Pricing API list-price for the
     instance type in the region.
3. Cost = integral of (price × time) from LaunchTime → TerminationTime.

Aggregate sum across instances = total bill.

Fallbacks:
* If ECS/EC2 resolution fails (terminated + GC'd, IAM denied), we skip
  that instance and log — the total is then a lower bound. The result
  includes ``unresolved_jobs`` so callers can see what was missed.
* If an instance is still running at report time, we use "now" as the
  termination time for the integral.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import boto3

from .collector import CollectReport
from .instance_resolver import (
    InstanceRecord,
    estimate_termination_from_jobs,
    resolve_instances,
)
from .poll import StatusSnapshot

LOG = logging.getLogger(__name__)


# Hardcoded fallback for the most common regions, used when SSM is
# unreachable (offline test runs or locked-down IAM).
_REGION_LOCATION_FALLBACK: dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "ca-central-1": "Canada (Central)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-north-1": "EU (Stockholm)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "sa-east-1": "South America (Sao Paulo)",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class InstanceCost:
    """Cost attribution for one EC2 instance."""

    instance_id: str
    instance_type: str
    availability_zone: str
    lifecycle: str                # "spot" or "on-demand"
    launch_time_ms: int
    termination_time_ms: int | None
    billable_seconds: float       # end - start, in seconds
    usd_total: float              # integral of price × time
    hourly_usd_avg: float         # usd_total / (billable_seconds/3600)
    # Price-variation summary over the instance lifespan [launch, term]:
    first_price_usd: float = 0.0
    """Price at launch_time_ms (usd/hour)."""
    last_price_usd: float = 0.0
    """Price at termination_time_ms, or at end_ms if still running (usd/hour)."""
    min_hourly_usd: float = 0.0
    """Minimum usd/hour observed during the lifespan."""
    max_hourly_usd: float = 0.0
    """Maximum usd/hour observed during the lifespan."""
    n_price_points: int = 0
    """Count of distinct constant-price segments during the lifespan.
    1 = price never changed. >1 = spot market moved during this instance."""
    price_points: list[tuple[int, float]] = field(default_factory=list)
    """Raw (epoch_ms, usd_per_hour) tuples from AWS, covering a window that
    overlaps the lifespan. For spot: from DescribeSpotPriceHistory.
    For on-demand: single point at launch_time."""
    job_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "instance_type": self.instance_type,
            "availability_zone": self.availability_zone,
            "lifecycle": self.lifecycle,
            "launch_time_ms": self.launch_time_ms,
            "termination_time_ms": self.termination_time_ms,
            "billable_seconds": round(self.billable_seconds, 2),
            "billable_hours": round(self.billable_seconds / 3600, 5),
            "usd_total": round(self.usd_total, 6),
            "hourly_usd_avg": round(self.hourly_usd_avg, 6),
            "first_price_usd": round(self.first_price_usd, 6),
            "last_price_usd": round(self.last_price_usd, 6),
            "min_hourly_usd": round(self.min_hourly_usd, 6),
            "max_hourly_usd": round(self.max_hourly_usd, 6),
            "n_price_points": self.n_price_points,
            "job_ids": list(self.job_ids),
            "n_jobs": len(self.job_ids),
        }


@dataclass
class CostEstimate:
    """Aggregate cost across the whole submission."""

    region: str
    per_instance: list[InstanceCost] = field(default_factory=list)
    unresolved_job_ids: list[str] = field(default_factory=list)
    """Jobs whose container_instance_arn couldn't be mapped to an EC2
    instance (typically: terminated + EC2 GC'd, or IAM denied). Their
    runtime is NOT counted in ``total_usd``."""

    # Workload metrics (handy to bundle here for $/token derivations)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_succeeded_requests: int = 0

    @property
    def total_usd(self) -> float:
        return round(sum(i.usd_total for i in self.per_instance), 6)

    @property
    def total_billable_seconds(self) -> float:
        return round(sum(i.billable_seconds for i in self.per_instance), 2)

    @property
    def total_billable_hours(self) -> float:
        return round(self.total_billable_seconds / 3600, 5)

    @property
    def instance_count(self) -> int:
        return len(self.per_instance)

    def _per_1m(self, tokens: int) -> float | None:
        if tokens <= 0 or not self.per_instance:
            return None
        return round(self.total_usd / tokens * 1_000_000, 4)

    def _per_1k(self, n: int) -> float | None:
        if n <= 0 or not self.per_instance:
            return None
        return round(self.total_usd / n * 1_000, 4)

    def as_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "instance_count": self.instance_count,
            "unresolved_job_count": len(self.unresolved_job_ids),
            "total_billable_seconds": self.total_billable_seconds,
            "total_billable_hours": self.total_billable_hours,
            "total_usd": self.total_usd,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_succeeded_requests": self.total_succeeded_requests,
            "usd_per_1m_input_tokens": self._per_1m(self.total_input_tokens),
            "usd_per_1m_output_tokens": self._per_1m(self.total_output_tokens),
            "usd_per_1m_total_tokens": self._per_1m(
                self.total_input_tokens + self.total_output_tokens
            ),
            "usd_per_1k_requests": self._per_1k(self.total_succeeded_requests),
            "per_instance": [i.to_dict() for i in self.per_instance],
            "unresolved_job_ids": list(self.unresolved_job_ids),
        }

    def price_timeline(self, instance_id: str) -> list[dict[str, Any]]:
        """Return per-segment price breakdown for one instance.

        For the given ``instance_id``, splits its [launch, termination]
        lifespan into constant-price segments (useful when spot price
        changed mid-lifespan). Each segment is:

        * ``segment_start_ms``, ``segment_end_ms``
        * ``duration_seconds``
        * ``hourly_usd`` — price that held during this segment
        * ``segment_cost_usd`` — duration × hourly_usd

        Empty list for unknown ``instance_id``, or when pricing couldn't be
        resolved for that instance.
        """
        for inst in self.per_instance:
            if inst.instance_id != instance_id:
                continue
            end_ms = inst.termination_time_ms
            if end_ms is None:
                # Instance still running or termination unknown — use the
                # billable_seconds we computed earlier to recover end_ms.
                end_ms = inst.launch_time_ms + int(inst.billable_seconds * 1000)
            segments = segments_for_lifespan(
                inst.price_points, inst.launch_time_ms, end_ms,
            )
            return [
                {
                    "segment_start_ms": s,
                    "segment_end_ms": e,
                    "duration_seconds": round((e - s) / 1000.0, 2),
                    "hourly_usd": round(price, 6),
                    "segment_cost_usd": round(
                        (e - s) / 1000.0 / 3600.0 * price, 6
                    ),
                }
                for s, e, price in segments
            ]
        return []


# ---------------------------------------------------------------------------
# Rate lookups
# ---------------------------------------------------------------------------
def resolve_location_name(region_code: str, *, ssm_client: Any | None = None) -> str:
    """Region code → human-readable Pricing-API location string.

    Tries SSM first (most up-to-date), falls back to a hardcoded table.
    Raises ValueError if neither source knows the region.
    """
    ssm = ssm_client or boto3.client("ssm", region_name="us-east-1")
    try:
        resp = ssm.get_parameter(
            Name=f"/aws/service/global-infrastructure/regions/{region_code}/longName"
        )
        return resp["Parameter"]["Value"]
    except Exception as exc:  # noqa: BLE001
        LOG.debug("SSM lookup for %s failed: %s; using fallback", region_code, exc)
        if region_code in _REGION_LOCATION_FALLBACK:
            return _REGION_LOCATION_FALLBACK[region_code]
        raise ValueError(
            f"Cannot resolve location name for region {region_code!r}. "
            f"Add it to _REGION_LOCATION_FALLBACK."
        ) from exc


def get_on_demand_hourly_rate(
    instance_type: str,
    region_code: str,
    *,
    pricing_client: Any | None = None,
    ssm_client: Any | None = None,
) -> float | None:
    """Return USD/hour on-demand rate for Linux shared-tenancy, or None."""
    pricing = pricing_client or boto3.client("pricing", region_name="us-east-1")
    try:
        location = resolve_location_name(region_code, ssm_client=ssm_client)
    except ValueError:
        return None

    filters = [
        {"Type": "TERM_MATCH", "Field": "serviceCode",      "Value": "AmazonEC2"},
        {"Type": "TERM_MATCH", "Field": "instanceType",     "Value": instance_type},
        {"Type": "TERM_MATCH", "Field": "location",         "Value": location},
        {"Type": "TERM_MATCH", "Field": "operatingSystem",  "Value": "Linux"},
        {"Type": "TERM_MATCH", "Field": "tenancy",          "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "preInstalledSw",   "Value": "NA"},
        {"Type": "TERM_MATCH", "Field": "capacitystatus",   "Value": "Used"},
        {"Type": "TERM_MATCH", "Field": "marketoption",     "Value": "OnDemand"},
    ]
    try:
        resp = pricing.get_products(
            ServiceCode="AmazonEC2", Filters=filters, MaxResults=5,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Pricing API get_products failed for %s in %s: %s",
                    instance_type, region_code, exc)
        return None

    prices: list[float] = []
    for price_list_entry in resp.get("PriceList", []):
        doc = (
            json.loads(price_list_entry)
            if isinstance(price_list_entry, str) else price_list_entry
        )
        on_demand = doc.get("terms", {}).get("OnDemand", {})
        for _sku, sku_terms in on_demand.items():
            for _pd, pd_val in sku_terms.get("priceDimensions", {}).items():
                usd_str = pd_val.get("pricePerUnit", {}).get("USD")
                if usd_str is not None:
                    try:
                        prices.append(float(usd_str))
                    except ValueError:
                        continue
    if not prices:
        return None
    return round(min(prices), 6)


def get_spot_price_history(
    instance_type: str,
    availability_zone: str,
    start_ms: int,
    end_ms: int,
    *,
    ec2_client: Any | None = None,
    region: str = "us-west-2",
) -> list[tuple[int, float]]:
    """Return all spot price points that applied during [start_ms, end_ms].

    Includes the last price BEFORE start_ms (that price was in effect
    when our window opened). Returns sorted list of (epoch_ms, usd/hour).
    Empty list on error — caller should fall back to on-demand rate.
    """
    ec2 = ec2_client or boto3.client("ec2", region_name=region)

    # Widen the window by a few hours to catch the "price before start".
    query_start_ms = start_ms - 6 * 60 * 60 * 1000   # 6h before launch
    from datetime import datetime, timezone
    query_start = datetime.fromtimestamp(query_start_ms / 1000, tz=timezone.utc)
    query_end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

    points: list[tuple[int, float]] = []
    try:
        paginator = ec2.get_paginator("describe_spot_price_history")
        for page in paginator.paginate(
            InstanceTypes=[instance_type],
            ProductDescriptions=["Linux/UNIX"],
            AvailabilityZone=availability_zone,
            StartTime=query_start,
            EndTime=query_end,
        ):
            for p in page.get("SpotPriceHistory", []):
                ts = p.get("Timestamp")
                price = p.get("SpotPrice")
                if ts is None or price is None:
                    continue
                epoch_ms = int(ts.timestamp() * 1000)
                try:
                    points.append((epoch_ms, float(price)))
                except ValueError:
                    continue
    except Exception as exc:  # noqa: BLE001
        LOG.warning(
            "DescribeSpotPriceHistory failed for %s/%s: %s",
            instance_type, availability_zone, exc,
        )
        return []

    points.sort(key=lambda p: p[0])
    return points


def integrate_price(
    price_points: list[tuple[int, float]],
    start_ms: int,
    end_ms: int,
) -> float:
    """Compute integral of price × time over [start_ms, end_ms].

    price_points: sorted list of (change_epoch_ms, usd_per_hour). Each
    point means "from this time onward, price is this value until the
    next point".

    Returns total USD cost for the window.
    """
    if end_ms <= start_ms or not price_points:
        return 0.0

    # Determine the price at start_ms: the latest point with ts <= start_ms.
    # If all points are after start_ms, use the first point's price (nothing
    # to use for "before", so assume it held).
    price_at_start: float | None = None
    for ts, price in price_points:
        if ts <= start_ms:
            price_at_start = price
        else:
            break
    if price_at_start is None:
        price_at_start = price_points[0][1]

    total_usd = 0.0
    current_price = price_at_start
    segment_start = start_ms

    for ts, price in price_points:
        if ts <= start_ms:
            continue
        if ts >= end_ms:
            break
        # Close out the segment [segment_start, ts] at current_price
        seg_hours = (ts - segment_start) / 1000.0 / 3600.0
        total_usd += seg_hours * current_price
        segment_start = ts
        current_price = price

    # Final segment [segment_start, end_ms]
    seg_hours = (end_ms - segment_start) / 1000.0 / 3600.0
    total_usd += seg_hours * current_price
    return total_usd


def segments_for_lifespan(
    price_points: list[tuple[int, float]],
    start_ms: int,
    end_ms: int,
) -> list[tuple[int, int, float]]:
    """Break [start_ms, end_ms] into constant-price segments.

    Uses the same semantics as :func:`integrate_price`: each ``price_points``
    entry means "from this timestamp onward, price holds until the next
    timestamp (or end_ms)". Any change points BEFORE start_ms only set the
    price at start_ms; they don't create a segment.

    Returns a list of ``(segment_start_ms, segment_end_ms, usd_per_hour)``
    with ``segment_end_ms > segment_start_ms``. Empty list if end_ms <=
    start_ms or no price points at all.
    """
    if end_ms <= start_ms or not price_points:
        return []

    # Price in effect at start_ms: latest change <= start_ms, or first point.
    price_at_start: float | None = None
    for ts, price in price_points:
        if ts <= start_ms:
            price_at_start = price
        else:
            break
    if price_at_start is None:
        price_at_start = price_points[0][1]

    segments: list[tuple[int, int, float]] = []
    current_price = price_at_start
    segment_start = start_ms

    for ts, price in price_points:
        if ts <= start_ms:
            continue
        if ts >= end_ms:
            break
        if ts > segment_start:
            segments.append((segment_start, ts, current_price))
        segment_start = ts
        current_price = price

    if end_ms > segment_start:
        segments.append((segment_start, end_ms, current_price))
    return segments


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def estimate_cost(
    *,
    collect_report: CollectReport,
    status_snapshot: StatusSnapshot,
    region: str,
    ecs_cluster_arn: str | None = None,
    ecs_client: Any | None = None,
    ec2_client: Any | None = None,
    pricing_client: Any | None = None,
    ssm_client: Any | None = None,
    now_ms: int | None = None,
) -> CostEstimate:
    """Compute the real AWS bill for the submission.

    Sums per-EC2-instance costs (integral of price × lifespan) rather
    than per-job costs. See module docstring for rationale.

    Parameters
    ----------
    collect_report, status_snapshot
        From ``download_outputs`` and ``wait_for_completion``.
    region
        AWS region code (e.g. ``"us-east-2"``).
    ecs_cluster_arn
        ECS cluster ARN for the Batch Compute Environment. If None,
        inferred from ``job.container_instance_arn``.
    now_ms
        Override for "current time" when computing cost of
        still-running instances. Defaults to ``time.time() * 1000``.

    Returns
    -------
    :class:`CostEstimate` with per-instance breakdown and aggregate totals.
    Instances that couldn't be resolved are listed under
    ``unresolved_job_ids`` and their runtime is excluded from ``total_usd``.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    # Token totals from the collector (same as before)
    total_in = sum(r.get("total_input_tokens", 0) or 0
                   for r in collect_report.per_shard_summary)
    total_out = sum(r.get("total_output_tokens", 0) or 0
                    for r in collect_report.per_shard_summary)
    succeeded = sum(r.get("succeeded", 0) or 0
                    for r in collect_report.per_shard_summary)

    # Step 1: resolve unique instances from the jobs.
    instance_records = resolve_instances(
        status_snapshot,
        ecs_cluster_arn=ecs_cluster_arn,
        region=region,
        ecs_client=ecs_client,
        ec2_client=ec2_client,
    )

    # Track which jobs ended up attributed to a resolved instance.
    resolved_job_ids: set[str] = set()
    per_instance: list[InstanceCost] = []

    # Cache pricing lookups per (instance_type, region) to avoid redundant
    # Pricing API calls when multiple instances share a type.
    on_demand_cache: dict[str, float | None] = {}

    for rec in instance_records:
        resolved_job_ids.update(rec.job_ids)

        # Determine window end: actual termination or now.
        end_ms = rec.termination_time_ms
        if end_ms is None:
            end_ms = estimate_termination_from_jobs(rec, status_snapshot)
        if end_ms is None:
            end_ms = now_ms

        start_ms = rec.launch_time_ms
        billable_seconds = max(0.0, (end_ms - start_ms) / 1000.0)

        # Price points + integration
        price_points: list[tuple[int, float]] = []
        usd_total = 0.0
        hourly_avg = 0.0

        if rec.lifecycle == "spot":
            price_points = get_spot_price_history(
                rec.instance_type,
                rec.availability_zone,
                start_ms=start_ms,
                end_ms=end_ms,
                ec2_client=ec2_client,
                region=region,
            )
            if price_points:
                usd_total = integrate_price(price_points, start_ms, end_ms)
                hourly_avg = (
                    usd_total / (billable_seconds / 3600.0)
                    if billable_seconds > 0 else 0.0
                )
            else:
                # No spot history — fall back to on-demand rate as best-effort.
                LOG.info(
                    "No spot price history for %s/%s; using on-demand rate as fallback",
                    rec.instance_type, rec.availability_zone,
                )
                if rec.instance_type not in on_demand_cache:
                    on_demand_cache[rec.instance_type] = get_on_demand_hourly_rate(
                        rec.instance_type, region,
                        pricing_client=pricing_client, ssm_client=ssm_client,
                    )
                rate = on_demand_cache[rec.instance_type]
                if rate is not None:
                    price_points = [(start_ms, rate)]
                    usd_total = billable_seconds / 3600.0 * rate
                    hourly_avg = rate
        else:  # on-demand
            if rec.instance_type not in on_demand_cache:
                on_demand_cache[rec.instance_type] = get_on_demand_hourly_rate(
                    rec.instance_type, region,
                    pricing_client=pricing_client, ssm_client=ssm_client,
                )
            rate = on_demand_cache[rec.instance_type]
            if rate is not None:
                price_points = [(start_ms, rate)]
                usd_total = billable_seconds / 3600.0 * rate
                hourly_avg = rate

        # Resolve price points (spot history, or a synthetic one-point list
        # for on-demand/fallback). Then integrate + summarize in one place.
        price_points: list[tuple[int, float]] = []

        if rec.lifecycle == "spot":
            price_points = get_spot_price_history(
                rec.instance_type,
                rec.availability_zone,
                start_ms=start_ms,
                end_ms=end_ms,
                ec2_client=ec2_client,
                region=region,
            )
            if not price_points:
                # No spot history — fall back to on-demand rate as best-effort.
                LOG.info(
                    "No spot price history for %s/%s; using on-demand rate as fallback",
                    rec.instance_type, rec.availability_zone,
                )
                if rec.instance_type not in on_demand_cache:
                    on_demand_cache[rec.instance_type] = get_on_demand_hourly_rate(
                        rec.instance_type, region,
                        pricing_client=pricing_client, ssm_client=ssm_client,
                    )
                rate = on_demand_cache[rec.instance_type]
                if rate is not None:
                    price_points = [(start_ms, rate)]
        else:  # on-demand
            if rec.instance_type not in on_demand_cache:
                on_demand_cache[rec.instance_type] = get_on_demand_hourly_rate(
                    rec.instance_type, region,
                    pricing_client=pricing_client, ssm_client=ssm_client,
                )
            rate = on_demand_cache[rec.instance_type]
            if rate is not None:
                price_points = [(start_ms, rate)]

        # Segment the lifespan + aggregate. All derived stats flow from here.
        segments = segments_for_lifespan(price_points, start_ms, end_ms)
        usd_total = integrate_price(price_points, start_ms, end_ms)
        hourly_avg = (
            usd_total / (billable_seconds / 3600.0)
            if billable_seconds > 0 else 0.0
        )
        if segments:
            first_price = segments[0][2]
            last_price = segments[-1][2]
            min_hourly = min(p for _s, _e, p in segments)
            max_hourly = max(p for _s, _e, p in segments)
            n_points = len(segments)
        else:
            first_price = last_price = min_hourly = max_hourly = 0.0
            n_points = 0

        per_instance.append(InstanceCost(
            instance_id=rec.instance_id,
            instance_type=rec.instance_type,
            availability_zone=rec.availability_zone,
            lifecycle=rec.lifecycle,
            launch_time_ms=start_ms,
            termination_time_ms=rec.termination_time_ms,
            billable_seconds=billable_seconds,
            usd_total=usd_total,
            hourly_usd_avg=hourly_avg,
            first_price_usd=first_price,
            last_price_usd=last_price,
            min_hourly_usd=min_hourly,
            max_hourly_usd=max_hourly,
            n_price_points=n_points,
            price_points=price_points,
            job_ids=list(rec.job_ids),
        ))

    # Unresolved jobs: had container_instance_arn but couldn't map to an instance.
    unresolved = [
        j.job_id for j in status_snapshot.jobs
        if j.job_id not in resolved_job_ids
    ]

    return CostEstimate(
        region=region,
        per_instance=per_instance,
        unresolved_job_ids=unresolved,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_succeeded_requests=succeeded,
    )


__all__ = [
    "CostEstimate",
    "InstanceCost",
    "estimate_cost",
    "get_on_demand_hourly_rate",
    "get_spot_price_history",
    "integrate_price",
    "segments_for_lifespan",
    "resolve_location_name",
]
