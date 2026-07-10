"""Hardware + pricing catalog service.

The :class:`Catalog` wraps a ``catalog_cache.json`` file (schema v2) and
provides a narrow, region-aware API so callers never construct spec objects
manually or hit AWS directly.

Cache schema v2::

    {
      "_meta": {
        "schema": 2,
        "hardware_refreshed_at": "2026-05-03T…",
        "prices_refreshed_at":   "2026-05-03T…",
        "regions": ["us-east-2", "us-east-1", "us-west-2"]
      },
      "hardware": {
        "g5.12xlarge": {<HardwareFacts fields>},
        ...
      },
      "prices": {
        "g5.12xlarge": {"us-east-2": 5.672, "us-east-1": 5.672},
        ...
      }
    }

Architecture
------------
The **code** for the catalog lives in this package, but the **data** (the
JSON file) lives at model level: ``models/<name>/catalog_cache.json``. This
framework has no default cache path and no hard-coded instance-type list;
both come from the model package.

Typical use from a model package::

    # models/medgemma_27b/__init__.py
    CATALOG_CACHE = Path(__file__).parent / "catalog_cache.json"
    INSTANCE_TYPES = sorted({c.deployment.instance_type for c in EXPERIMENTS.values()})

    def load_catalog(**kw):
        return Catalog(CATALOG_CACHE).load(**kw)

Capacity Block prices are NOT stored — they're offering-driven (purchase
time). Use :meth:`Catalog.live_spot` or direct
``DescribeCapacityBlockOfferings`` calls for anything live.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .hardware_facts import HardwareFacts

LOG = logging.getLogger(__name__)

_CACHE_SCHEMA_VERSION = 2


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------
class CatalogStaleError(RuntimeError):
    """Raised when the cache is missing/stale and a live refresh failed with offline_ok=False."""


class CatalogNotLoaded(RuntimeError):
    """Raised when callers query a Catalog that hasn't been populated yet."""


# -----------------------------------------------------------------------------
# Catalog
# -----------------------------------------------------------------------------
class Catalog:
    """Hardware + pricing service backed by a ``catalog_cache.json`` file.

    The cache path is **required** — this framework has no opinion on where
    catalog data lives. Model packages (under ``models/<name>/``) own their
    own cache file.

    Example::

        cat = Catalog(Path("models/medgemma_27b/catalog_cache.json")).load(
            offline_ok=False,   # strict — fail if AWS is unreachable
        )
        cat.hardware("g5.12xlarge").num_accelerators          # → 4
        cat.price_od("g5.12xlarge", "us-east-2")              # → 5.672
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(self, cache_path: Path | str) -> None:
        self._cache_path = Path(cache_path)
        self._hardware: dict[str, HardwareFacts] = {}
        self._prices: dict[str, dict[str, float]] = {}
        self._meta: dict[str, Any] = {}
        self._loaded: bool = False

    # ------------------------------------------------------------------
    # Public API — loading
    # ------------------------------------------------------------------
    def load(
        self,
        *,
        auto_refresh: bool = True,
        max_age_hours_prices: int = 24,
        max_age_hours_hardware: int = 24 * 30,  # hardware barely changes; 30 days is fine
        refresh_regions: list[str] | None = None,
        refresh_instance_types: list[str] | None = None,
        offline_ok: bool = True,
    ) -> "Catalog":
        """Populate this Catalog from the cache (+ optional live refresh).

        Parameters
        ----------
        auto_refresh
            If True (default), trigger :meth:`refresh` when the relevant
            section of the cache is missing or stale.
        max_age_hours_prices, max_age_hours_hardware
            Staleness thresholds per section. Prices default to 24h; hardware
            to 30d (it basically never changes).
        refresh_regions
            Passed through to :meth:`refresh` — which regions to price in.
            Required if auto_refresh may trigger (no framework default).
        refresh_instance_types
            Passed through to :meth:`refresh`. Required if auto_refresh may
            trigger — the framework has no default list.
        offline_ok
            True (default) = graceful fallback to cache on refresh failure.
            False = raise :class:`CatalogStaleError` instead (use for
            production notebook runs where stale prices matter).

        Returns
        -------
        self (fluent).
        """
        prices_need = self._needs_refresh("prices_refreshed_at", max_age_hours_prices)
        hardware_need = self._needs_refresh("hardware_refreshed_at", max_age_hours_hardware)

        cache_exists = self._cache_path.exists()
        if auto_refresh and (not cache_exists or prices_need or hardware_need):
            if not refresh_instance_types:
                raise ValueError(
                    "Catalog.load(auto_refresh=True) requires refresh_instance_types= "
                    "when the cache is missing or stale. Pass the list from your model "
                    "package, e.g. INSTANCE_TYPES from models/<name>/__init__.py."
                )
            if not refresh_regions:
                raise ValueError(
                    "Catalog.load(auto_refresh=True) requires refresh_regions= when a "
                    "refresh is needed."
                )
            try:
                LOG.info(
                    "Refreshing catalog (cache_exists=%s, hardware_stale=%s, prices_stale=%s)",
                    cache_exists, hardware_need, prices_need,
                )
                self.refresh(
                    regions=refresh_regions,
                    instance_types=refresh_instance_types,
                    hardware=hardware_need or not cache_exists,
                    prices=prices_need or not cache_exists,
                )
                self._loaded = True
                return self
            except Exception as exc:  # noqa: BLE001
                if offline_ok:
                    LOG.warning(
                        "⚠️  Catalog refresh FAILED (%s). Falling back to on-disk "
                        "cache — prices may be stale. Set offline_ok=False to "
                        "fail loudly in production.", exc,
                    )
                else:
                    raise CatalogStaleError(
                        f"Catalog refresh failed and offline_ok=False: {exc}"
                    ) from exc

        # Read whatever's on disk
        if not cache_exists:
            if not offline_ok:
                raise CatalogStaleError(
                    f"No catalog cache at {self._cache_path}. "
                    "Call .refresh(instance_types=..., regions=...) first."
                )
            LOG.warning(
                "⚠️  No catalog cache at %s and refresh unavailable. Catalog "
                "will be EMPTY; downstream queries will raise CatalogNotLoaded.",
                self._cache_path,
            )
            self._loaded = True  # we tried; nothing to load
            return self

        self._read_from_disk()
        self._loaded = True
        return self

    def refresh(
        self,
        *,
        instance_types: list[str],
        regions: list[str],
        regions_extend: list[str] | None = None,
        hardware: bool = True,
        prices: bool = True,
    ) -> "Catalog":
        """Hit AWS for hardware facts and/or prices, update + persist the cache.

        Parameters
        ----------
        instance_types
            Required. The list of EC2 instance types to fetch. There is no
            framework default — callers must supply the list (typically
            ``INSTANCE_TYPES`` from their model package).
        regions
            Required. Regions to price in.
        regions_extend
            Optional. Additional regions to append to ``regions``.
        hardware, prices
            Control which sections are refreshed. Default = both. If one is
            False, the other section is preserved as-is.

        If ``hardware=False`` and ``prices=True``, existing hardware entries
        are preserved and only the prices section is updated. Same in reverse.

        Returns self.
        """
        import boto3
        from .aws_sources import fetch_hardware_from_describe_instance_types, fetch_on_demand_price

        if not (hardware or prices):
            raise ValueError("At least one of hardware= or prices= must be True.")
        if not instance_types:
            raise ValueError("instance_types must be a non-empty list.")
        if not regions:
            raise ValueError("regions must be a non-empty list.")

        all_regions = list(regions)
        if regions_extend:
            for r in regions_extend:
                if r not in all_regions:
                    all_regions.append(r)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Read any existing cache first so partial refresh preserves the other section
        if self._cache_path.exists() and not (self._hardware or self._prices):
            try:
                self._read_from_disk()
            except Exception:  # noqa: BLE001
                LOG.debug("Could not pre-read cache; starting fresh.", exc_info=True)

        ec2_by_region: dict[str, Any] = {}
        pricing_client = boto3.client("pricing", region_name="us-east-1") if prices else None

        for it in instance_types:
            # --- hardware ---------------------------------------------------
            if hardware:
                facts: HardwareFacts | None = None
                for region in all_regions:
                    try:
                        if region not in ec2_by_region:
                            ec2_by_region[region] = boto3.client("ec2", region_name=region)
                        fields = fetch_hardware_from_describe_instance_types(
                            it, ec2_by_region[region],
                        )
                        facts = HardwareFacts(instance_type=it, **fields)
                        break
                    except Exception as exc:  # noqa: BLE001
                        LOG.debug("Hardware fetch %s in %s failed: %s", it, region, exc)
                        continue
                if facts is None:
                    LOG.error(
                        "%s: hardware fetch failed in ALL regions %s — skipping. "
                        "If this instance was previously cached, the old entry is "
                        "retained.", it, all_regions,
                    )
                else:
                    self._hardware[it] = facts

            # --- prices -----------------------------------------------------
            if prices:
                new_prices: dict[str, float] = {}
                for region in all_regions:
                    try:
                        p = fetch_on_demand_price(it, region, pricing_client)
                        if p is not None:
                            new_prices[region] = p
                    except Exception as exc:  # noqa: BLE001
                        LOG.debug("Price fetch %s in %s failed: %s", it, region, exc)
                        continue
                # Merge with existing prices so non-refreshed regions aren't lost
                existing = self._prices.get(it, {})
                existing_other_regions = {
                    r: p for r, p in existing.items() if r not in all_regions
                }
                merged = {**existing_other_regions, **new_prices}
                if merged:
                    self._prices[it] = merged
                elif it in self._prices:
                    # All regions we queried returned nothing and we had no other regions.
                    del self._prices[it]

            LOG.info(
                "Refreshed %s (hardware=%s, prices=%d regions)",
                it, hardware, len(self._prices.get(it, {})),
            )

        # --- meta + persist -----------------------------------------------
        # Preserve just the opposite section's timestamp if we only refreshed one.
        kept_hardware_at = self._meta.get("hardware_refreshed_at") if not hardware else None
        kept_prices_at = self._meta.get("prices_refreshed_at") if not prices else None
        self._meta = {
            "schema": _CACHE_SCHEMA_VERSION,
            "regions": all_regions,
        }
        if hardware:
            self._meta["hardware_refreshed_at"] = now
        elif kept_hardware_at is not None:
            self._meta["hardware_refreshed_at"] = kept_hardware_at
        if prices:
            self._meta["prices_refreshed_at"] = now
        elif kept_prices_at is not None:
            self._meta["prices_refreshed_at"] = kept_prices_at
        self._write_to_disk()
        self._loaded = True
        return self

    # ------------------------------------------------------------------
    # Public API — queries
    # ------------------------------------------------------------------
    def hardware(self, instance_type: str) -> HardwareFacts:
        """Return :class:`HardwareFacts` for ``instance_type``.

        Raises
        ------
        CatalogNotLoaded
            If :meth:`load` hasn't run.
        KeyError
            If ``instance_type`` isn't in the catalog.
        """
        self._ensure_loaded()
        try:
            return self._hardware[instance_type]
        except KeyError as exc:
            raise KeyError(
                f"No hardware facts for {instance_type!r}. "
                f"Known: {sorted(self._hardware)}. "
                "Refresh the catalog with this instance type in the instance_types= list."
            ) from exc

    def price_od(self, instance_type: str, region: str) -> float | None:
        """On-demand USD/hr for ``instance_type`` in ``region``, or None.

        Returns None either when the instance has no OD pricing at all
        (p5e is Capacity-Block only) or when this specific region wasn't
        captured during the last :meth:`refresh`. Re-run refresh with
        ``regions_extend=[region]`` to add a new region.
        """
        self._ensure_loaded()
        return self._prices.get(instance_type, {}).get(region)

    def price_od_all(self, instance_type: str) -> dict[str, float]:
        """All known (region → OD price) entries for ``instance_type``."""
        self._ensure_loaded()
        return dict(self._prices.get(instance_type, {}))

    def estimated_spot(
        self, instance_type: str, region: str, *, discount: float = 0.30,
    ) -> float | None:
        """Heuristic spot price: OD × (1 − discount). None if no OD price."""
        od = self.price_od(instance_type, region)
        if od is None:
            return None
        return round(od * (1 - discount), 4)

    def live_spot(self, instance_type: str, region: str) -> float | None:
        """Hit ``DescribeSpotPriceHistory`` for the most recent spot price.

        Not cached — every call is a live API roundtrip. Use sparingly.
        Returns None on any API error or empty history.
        """
        import boto3
        try:
            ec2 = boto3.client("ec2", region_name=region)
            resp = ec2.describe_spot_price_history(
                InstanceTypes=[instance_type],
                ProductDescriptions=["Linux/UNIX"],
                MaxResults=1,
            )
            hist = resp.get("SpotPriceHistory", [])
            return float(hist[0]["SpotPrice"]) if hist else None
        except Exception as exc:  # noqa: BLE001
            LOG.warning(
                "live_spot(%s, %s) failed: %s", instance_type, region, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def meta(self) -> dict[str, Any]:
        """The cache's ``_meta`` block, or empty dict if not loaded."""
        return dict(self._meta)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def instance_types(self) -> list[str]:
        """Instance types with hardware facts in the catalog (sorted)."""
        self._ensure_loaded()
        return sorted(self._hardware)

    def regions_priced(self, instance_type: str) -> list[str]:
        """Regions with OD pricing for the given instance (sorted)."""
        self._ensure_loaded()
        return sorted(self._prices.get(instance_type, {}))

    def is_stale(
        self,
        *,
        max_age_hours_prices: int = 24,
        max_age_hours_hardware: int = 24 * 30,
    ) -> bool:
        """True if either section is older than its threshold."""
        return (
            self._needs_refresh("prices_refreshed_at", max_age_hours_prices)
            or self._needs_refresh("hardware_refreshed_at", max_age_hours_hardware)
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if not self._loaded:
            raise CatalogNotLoaded(
                "Catalog hasn't been loaded. Call .load() first."
            )

    def _read_from_disk(self) -> None:
        """Parse the cache JSON (schema v1 or v2) into in-memory structures."""
        raw = json.loads(self._cache_path.read_text())
        self._meta = dict(raw.get("_meta", {}))
        schema = self._meta.get("schema", 1)

        if schema == 1:
            LOG.info("Migrating v1 cache → v2 in memory.")
            self._parse_v1(raw)
        elif schema == 2:
            self._parse_v2(raw)
        else:
            raise ValueError(
                f"Unsupported catalog cache schema: {schema}. "
                "Expected 1 or 2; re-run .refresh() to produce a current version."
            )

    def _parse_v2(self, raw: dict[str, Any]) -> None:
        self._hardware = {}
        self._prices = {}
        for it, payload in (raw.get("hardware") or {}).items():
            try:
                self._hardware[it] = HardwareFacts(**payload)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Skipping hardware %s from cache: %s", it, exc)
        for it, regions_map in (raw.get("prices") or {}).items():
            if isinstance(regions_map, dict):
                self._prices[it] = {
                    r: float(v) for r, v in regions_map.items()
                    if isinstance(v, (int, float)) and v > 0
                }

    def _parse_v1(self, raw: dict[str, Any]) -> None:
        """Parse v1 (HardwareSpec with embedded prices) into the v2 shape in memory."""
        self._hardware = {}
        self._prices = {}
        for it, payload in raw.items():
            if it.startswith("_"):
                continue
            try:
                hw_fields = {
                    k: payload[k]
                    for k in (
                        "family", "accelerator_model", "accelerator_architecture",
                        "num_accelerators", "vram_gib_per_accelerator", "vcpu", "ram_gib",
                    )
                    if k in payload
                }
                self._hardware[it] = HardwareFacts(instance_type=it, **hw_fields)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Skipping hardware %s (v1 migration): %s", it, exc)
                continue
            od = payload.get("on_demand_usd_per_hour")
            if isinstance(od, dict):
                self._prices[it] = {
                    r: float(v) for r, v in od.items() if isinstance(v, (int, float)) and v > 0
                }
            elif isinstance(od, (int, float)) and od > 0:
                self._prices[it] = {"unknown": float(od)}

    def _write_to_disk(self) -> None:
        payload: dict[str, Any] = {
            "_meta": dict(self._meta),
            "hardware": {it: facts.model_dump() for it, facts in sorted(self._hardware.items())},
            "prices": {
                it: dict(sorted(regions.items()))
                for it, regions in sorted(self._prices.items())
            },
        }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(payload, indent=2))
        LOG.info(
            "Wrote catalog cache: %d hardware, %d priced instances → %s",
            len(self._hardware), len(self._prices), self._cache_path,
        )

    def _needs_refresh(self, meta_key: str, max_age_hours: int) -> bool:
        if not self._cache_path.exists():
            return True
        try:
            raw = json.loads(self._cache_path.read_text())
            iso = (raw.get("_meta") or {}).get(meta_key)
        except json.JSONDecodeError:
            return True
        if not iso:
            return True
        try:
            when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) - when > timedelta(hours=max_age_hours)
        except ValueError:
            return True


# -----------------------------------------------------------------------------
# Module-level helpers
# -----------------------------------------------------------------------------
def catalog_meta(path: Path | str) -> dict[str, Any]:
    """Read the ``_meta`` block from a cache file without constructing a Catalog.

    Returns an empty dict if the file is missing or malformed.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return dict(json.loads(p.read_text()).get("_meta") or {})
    except (json.JSONDecodeError, OSError):
        return {}


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def _main() -> int:
    """CLI for refreshing a model's catalog cache.

    Usage::

        python -m vllm_ec2_bench.data.catalog --refresh --model medgemma_27b
        python -m vllm_ec2_bench.data.catalog --refresh \\
            --cache-path models/foo/catalog_cache.json \\
            --instance-types g5.12xlarge,p4d.24xlarge \\
            --regions us-east-2,us-east-1

    Refreshing always targets a specific cache path. The usual way is
    ``--model NAME`` which imports ``models.<name>`` and reads its
    ``CATALOG_CACHE`` + ``INSTANCE_TYPES`` + ``DEFAULT_REGIONS`` attributes.
    """
    import argparse
    import importlib

    parser = argparse.ArgumentParser(
        prog="python -m vllm_ec2_bench.data.catalog",
        description="Refresh a model's hardware + pricing catalog cache from AWS APIs.",
    )
    parser.add_argument("--refresh", action="store_true", help="Refresh the cache")
    parser.add_argument(
        "--model",
        help=(
            "Model package name under models/ (e.g. 'medgemma_27b'). Reads "
            "CATALOG_CACHE, INSTANCE_TYPES, DEFAULT_REGIONS from the package. "
            "Mutually exclusive with --cache-path/--instance-types/--regions."
        ),
    )
    parser.add_argument(
        "--cache-path", type=Path,
        help="Path to catalog_cache.json (escape hatch; use --model in normal use).",
    )
    parser.add_argument(
        "--instance-types", type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        help="Comma-separated list, e.g. 'g5.12xlarge,p4d.24xlarge'.",
    )
    parser.add_argument(
        "--regions", type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        help="Comma-separated list, e.g. 'us-east-2,us-east-1,us-west-2'.",
    )
    parser.add_argument(
        "--regions-extend",
        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        default=None,
        help="Regions to APPEND to --regions (or the model's DEFAULT_REGIONS).",
    )
    parser.add_argument("--hardware-only", action="store_true",
                        help="Refresh only the hardware section (leaves prices as-is).")
    parser.add_argument("--prices-only", action="store_true",
                        help="Refresh only the prices section (leaves hardware as-is).")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING - min(args.verbose, 2) * 10,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.refresh:
        parser.print_help()
        return 2

    if args.hardware_only and args.prices_only:
        parser.error("--hardware-only and --prices-only are mutually exclusive.")

    # --model vs. explicit path+types+regions: pick one.
    if args.model:
        if args.cache_path or args.instance_types or args.regions:
            parser.error(
                "--model is mutually exclusive with --cache-path/--instance-types/--regions."
            )
        try:
            mod = importlib.import_module(f"models.{args.model}")
        except ImportError as exc:
            parser.error(f"Could not import models.{args.model}: {exc}")
        try:
            cache_path = Path(mod.CATALOG_CACHE)
            instance_types = list(mod.INSTANCE_TYPES)
            regions = list(getattr(mod, "DEFAULT_REGIONS", ["us-east-2", "us-east-1", "us-west-2"]))
        except AttributeError as exc:
            parser.error(
                f"models.{args.model} is missing required attributes "
                f"(CATALOG_CACHE + INSTANCE_TYPES): {exc}"
            )
    else:
        missing = [
            name for name, val in (
                ("--cache-path", args.cache_path),
                ("--instance-types", args.instance_types),
                ("--regions", args.regions),
            ) if not val
        ]
        if missing:
            parser.error(
                f"Without --model, you must provide {', '.join(missing)}."
            )
        cache_path = args.cache_path
        instance_types = args.instance_types
        regions = args.regions

    hardware = not args.prices_only
    prices = not args.hardware_only

    cat = Catalog(cache_path)
    cat.refresh(
        regions=regions,
        regions_extend=args.regions_extend,
        instance_types=instance_types,
        hardware=hardware,
        prices=prices,
    )
    print(
        f"Refreshed {len(cat.instance_types())} instances "
        f"({sum(1 for it in cat.instance_types() if cat.regions_priced(it))} priced) "
        f"→ {cache_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "Catalog",
    "CatalogNotLoaded",
    "CatalogStaleError",
    "catalog_meta",
]
