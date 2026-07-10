"""Tests for the Catalog service (schema v2).

Covers:
* Loading from a fresh v2 cache.
* Auto-refresh triggered by stale cache.
* Validation: auto_refresh=True requires refresh_instance_types + refresh_regions.
* offline_ok=True falls back to cache; offline_ok=False raises.
* catalog_meta reads _meta from a given path (no default).
* v1 → v2 migration reads old-shape caches.
* Per-model factory (models.medgemma_27b) wires up the right cache path.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from vllm_ec2_bench import (
    Catalog,
    CatalogNotLoaded,
    CatalogStaleError,
    catalog_meta,
)

# Defaults used by the stale-refresh tests (must be non-empty to pass new
# required-args validation on Catalog.load).
_DUMMY_TYPES = ["g5.12xlarge"]
_DUMMY_REGIONS = ["us-east-2"]


# ---------------------------------------------------------------------------
# Cache fixtures
# ---------------------------------------------------------------------------
def _write_v2_cache(
    path: Path,
    *,
    prices_age_hours: float = 0.0,
    hardware_age_hours: float = 0.0,
    instance_type: str = "g5.12xlarge",
    regions: list[str] | None = None,
    od_price: float = 5.672,
) -> None:
    regions = regions or ["us-east-2", "us-east-1", "us-west-2"]
    now = datetime.now(timezone.utc)
    payload = {
        "_meta": {
            "schema": 2,
            "regions": regions,
            "hardware_refreshed_at": (now - timedelta(hours=hardware_age_hours)).isoformat(timespec="seconds"),
            "prices_refreshed_at":   (now - timedelta(hours=prices_age_hours)).isoformat(timespec="seconds"),
        },
        "hardware": {
            instance_type: {
                "instance_type": instance_type,
                "family": "gpu",
                "accelerator_model": "NVIDIA A10G",
                "accelerator_architecture": "Ampere",
                "num_accelerators": 4,
                "vram_gib_per_accelerator": 22.4,
                "vcpu": 48,
                "ram_gib": 192,
            }
        },
        "prices": {
            instance_type: {regions[0]: od_price},
        },
    }
    path.write_text(json.dumps(payload))


def _write_v1_cache(path: Path, *, instance_type: str = "g5.12xlarge") -> None:
    """Write an old-style v1 cache for migration testing."""
    payload = {
        "_meta": {
            "schema": 1,
            "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        instance_type: {
            "instance_type": instance_type,
            "family": "gpu",
            "accelerator_model": "NVIDIA A10G",
            "accelerator_architecture": "Ampere",
            "num_accelerators": 4,
            "vram_gib_per_accelerator": 22.4,
            "vcpu": 48,
            "ram_gib": 192,
            "on_demand_usd_per_hour": {"us-east-2": 5.672},
        },
    }
    path.write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# catalog_meta + Catalog basics
# ---------------------------------------------------------------------------
class TestCatalogMeta:
    def test_empty_when_no_cache(self, tmp_path: Path) -> None:
        assert catalog_meta(tmp_path / "nope.json") == {}

    def test_reads_meta(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.json"
        _write_v2_cache(cache)
        meta = catalog_meta(cache)
        assert meta["schema"] == 2
        assert "prices_refreshed_at" in meta


class TestCatalogBasicLoad:
    def test_fresh_v2_cache_loads_without_refresh(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.json"
        _write_v2_cache(cache, prices_age_hours=1, hardware_age_hours=1)
        cat = Catalog(cache).load(auto_refresh=False)

        assert cat.instance_types() == ["g5.12xlarge"]
        facts = cat.hardware("g5.12xlarge")
        assert facts.num_accelerators == 4
        assert cat.price_od("g5.12xlarge", "us-east-2") == 5.672
        assert cat.price_od("g5.12xlarge", "eu-west-1") is None  # not captured

    def test_queries_before_load_raise(self, tmp_path: Path) -> None:
        cat = Catalog(tmp_path / "nope.json")
        with pytest.raises(CatalogNotLoaded):
            cat.hardware("g5.12xlarge")
        with pytest.raises(CatalogNotLoaded):
            cat.price_od("g5.12xlarge", "us-east-2")

    def test_unknown_instance_type_raises(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.json"
        _write_v2_cache(cache)
        cat = Catalog(cache).load(auto_refresh=False)
        with pytest.raises(KeyError, match="No hardware facts"):
            cat.hardware("fake.99xlarge")

    def test_cache_path_property(self, tmp_path: Path) -> None:
        cache = tmp_path / "x.json"
        cat = Catalog(cache)
        assert cat.cache_path == cache


# ---------------------------------------------------------------------------
# Required-args validation on load
# ---------------------------------------------------------------------------
class TestLoadValidation:
    def test_auto_refresh_no_cache_requires_instance_types(self, tmp_path: Path) -> None:
        cache = tmp_path / "missing.json"
        with pytest.raises(ValueError, match="refresh_instance_types"):
            Catalog(cache).load(
                auto_refresh=True,
                refresh_regions=_DUMMY_REGIONS,
                # refresh_instance_types missing
            )

    def test_auto_refresh_no_cache_requires_regions(self, tmp_path: Path) -> None:
        cache = tmp_path / "missing.json"
        with pytest.raises(ValueError, match="refresh_regions"):
            Catalog(cache).load(
                auto_refresh=True,
                refresh_instance_types=_DUMMY_TYPES,
                # refresh_regions missing
            )

    def test_auto_refresh_false_no_validation_needed(self, tmp_path: Path) -> None:
        # Without auto_refresh, stale cache + no refresh args is fine.
        cache = tmp_path / "cache.json"
        _write_v2_cache(cache, prices_age_hours=100)
        cat = Catalog(cache).load(auto_refresh=False)
        assert cat.is_loaded


# ---------------------------------------------------------------------------
# Freshness + offline_ok
# ---------------------------------------------------------------------------
class TestFreshness:
    def test_stale_prices_trigger_refresh(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.json"
        _write_v2_cache(cache, prices_age_hours=100, hardware_age_hours=1)

        with patch.object(Catalog, "refresh", autospec=True) as mock_refresh:
            def _fake(self, **_kw):
                self._loaded = True
                return self
            mock_refresh.side_effect = _fake
            Catalog(cache).load(
                auto_refresh=True,
                max_age_hours_prices=24,
                max_age_hours_hardware=24 * 30,
                refresh_regions=_DUMMY_REGIONS,
                refresh_instance_types=_DUMMY_TYPES,
            )
            assert mock_refresh.called

    def test_fresh_both_sections_skip_refresh(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.json"
        _write_v2_cache(cache, prices_age_hours=1, hardware_age_hours=1)

        with patch.object(Catalog, "refresh", autospec=True) as mock_refresh:
            # No refresh args needed — fresh cache means no refresh triggered.
            Catalog(cache).load(auto_refresh=True)
            mock_refresh.assert_not_called()


class TestOfflineOk:
    def test_offline_ok_true_falls_back_on_refresh_failure(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.json"
        _write_v2_cache(cache, prices_age_hours=100)

        with patch.object(Catalog, "refresh", autospec=True) as mock_refresh:
            mock_refresh.side_effect = RuntimeError("no creds")
            cat = Catalog(cache).load(
                auto_refresh=True,
                offline_ok=True,
                refresh_regions=_DUMMY_REGIONS,
                refresh_instance_types=_DUMMY_TYPES,
            )
            # Stale but at least usable
            assert cat.is_loaded
            assert cat.instance_types() == ["g5.12xlarge"]

    def test_offline_ok_false_raises_on_refresh_failure(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.json"
        _write_v2_cache(cache, prices_age_hours=100)

        with patch.object(Catalog, "refresh", autospec=True) as mock_refresh:
            mock_refresh.side_effect = RuntimeError("no creds")
            with pytest.raises(CatalogStaleError, match="offline_ok=False"):
                Catalog(cache).load(
                    auto_refresh=True,
                    offline_ok=False,
                    refresh_regions=_DUMMY_REGIONS,
                    refresh_instance_types=_DUMMY_TYPES,
                )

    def test_offline_ok_false_no_cache_raises(self, tmp_path: Path) -> None:
        cache = tmp_path / "missing.json"
        with patch.object(Catalog, "refresh", autospec=True) as mock_refresh:
            mock_refresh.side_effect = RuntimeError("no creds")
            with pytest.raises(CatalogStaleError):
                Catalog(cache).load(
                    auto_refresh=True,
                    offline_ok=False,
                    refresh_regions=_DUMMY_REGIONS,
                    refresh_instance_types=_DUMMY_TYPES,
                )

    def test_offline_ok_true_no_cache_returns_empty(self, tmp_path: Path) -> None:
        cache = tmp_path / "missing.json"
        with patch.object(Catalog, "refresh", autospec=True) as mock_refresh:
            mock_refresh.side_effect = RuntimeError("no creds")
            cat = Catalog(cache).load(
                auto_refresh=True,
                offline_ok=True,
                refresh_regions=_DUMMY_REGIONS,
                refresh_instance_types=_DUMMY_TYPES,
            )
            assert cat.is_loaded
            assert cat.instance_types() == []


# ---------------------------------------------------------------------------
# v1 migration
# ---------------------------------------------------------------------------
class TestV1Migration:
    def test_reads_v1_cache_in_memory(self, tmp_path: Path) -> None:
        cache = tmp_path / "v1.json"
        _write_v1_cache(cache)
        cat = Catalog(cache).load(auto_refresh=False)
        assert cat.hardware("g5.12xlarge").num_accelerators == 4
        assert cat.price_od("g5.12xlarge", "us-east-2") == 5.672


# ---------------------------------------------------------------------------
# Refresh requires non-empty lists
# ---------------------------------------------------------------------------
class TestRefreshValidation:
    def test_refresh_empty_instance_types(self, tmp_path: Path) -> None:
        cat = Catalog(tmp_path / "x.json")
        with pytest.raises(ValueError, match="instance_types"):
            cat.refresh(instance_types=[], regions=["us-east-2"])

    def test_refresh_empty_regions(self, tmp_path: Path) -> None:
        cat = Catalog(tmp_path / "x.json")
        with pytest.raises(ValueError, match="regions"):
            cat.refresh(instance_types=["g5.12xlarge"], regions=[])

    def test_refresh_neither_section(self, tmp_path: Path) -> None:
        cat = Catalog(tmp_path / "x.json")
        with pytest.raises(ValueError, match="hardware"):
            cat.refresh(
                instance_types=["g5.12xlarge"], regions=["us-east-2"],
                hardware=False, prices=False,
            )


# ---------------------------------------------------------------------------
# Per-model factory — validates that data lives at model level
# ---------------------------------------------------------------------------
class TestModelLevelCatalog:
    """These tests exercise the medgemma_27b factory as a concrete reference.

    They're lightweight — they don't hit AWS — but they verify the wiring:
    cache path, instance types derived from experiments, factory import.
    """

    def test_medgemma_wiring(self) -> None:
        from models.medgemma_27b import (
            CATALOG_CACHE, DEFAULT_REGIONS, EXPERIMENTS, INSTANCE_TYPES,
        )

        # Cache lives at model level, not inside src/.
        assert CATALOG_CACHE.name == "catalog_cache.json"
        assert "models/medgemma_27b" in str(CATALOG_CACHE)
        assert "src/vllm_ec2_bench" not in str(CATALOG_CACHE)

        # INSTANCE_TYPES is derived from EXPERIMENTS — no hand-maintained list.
        derived = sorted({c.deployment.instance_type for c in EXPERIMENTS.values()})
        assert INSTANCE_TYPES == derived

        assert DEFAULT_REGIONS[0] == "us-west-2"

    def test_load_catalog_factory_offline(self) -> None:
        """Factory returns a loaded Catalog pointing at the model-level cache."""
        from models.medgemma_27b import load_catalog, CATALOG_CACHE

        if not CATALOG_CACHE.exists():
            pytest.skip("Catalog cache not present (fresh clone before refresh).")

        cat = load_catalog(offline_ok=True, auto_refresh=False)
        assert cat.is_loaded
        assert cat.cache_path == CATALOG_CACHE
        assert len(cat.instance_types()) >= 1
