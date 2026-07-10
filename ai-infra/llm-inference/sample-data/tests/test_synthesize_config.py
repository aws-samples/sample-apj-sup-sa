"""Static tests for the synthesizer.

These run without AWS creds. They verify the constants the rest of the repo
depends on (Nova model id pinned, exactly 10 seeds, expected seed names) so
that someone editing ``synthesize.py`` is forced to update the tests if they
change the contract.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_synthesize_module():
    if "synthesize" in sys.modules:
        return sys.modules["synthesize"]
    spec = importlib.util.spec_from_file_location(
        "synthesize", ROOT / "sample-data" / "scripts" / "synthesize.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Must be registered in sys.modules before exec_module so that @dataclass
    # can resolve the module's namespace during class creation.
    sys.modules["synthesize"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_default_model_is_nova_2_lite():
    """Synthesis MUST use Nova 2 Lite. No exceptions."""
    mod = _load_synthesize_module()
    assert mod.DEFAULT_MODEL_ID == "us.amazon.nova-2-lite-v1:0"


def test_domain_is_travel():
    """The shipped dataset is a single domain (travel)."""
    mod = _load_synthesize_module()
    assert mod.DOMAIN == "travel"


def test_exactly_10_seeds():
    """The dataset is 10 seeds × N records each (1K shipped, 10K at full scale)."""
    mod = _load_synthesize_module()
    assert len(mod.TRAVEL_SEEDS) == 10


def test_travel_seed_names_match_readme():
    mod = _load_synthesize_module()
    expected = {
        "domestic-flight", "international-flight", "train-booking",
        "bus-booking", "hotel-only", "car-rental", "flight-hotel-package",
        "multi-city", "cruise", "budget-airline",
    }
    assert {s.name for s in mod.TRAVEL_SEEDS} == expected


def test_existing_jsonl_files_have_expected_schema():
    """If sample-data/travel/ has been populated, every line must parse and
    expose ``text`` (non-empty) + ``meta.domain == 'travel'``. Skips if no
    files yet (e.g. fresh checkout)."""
    import json
    domain_dir = ROOT / "sample-data" / "travel"
    files = sorted(domain_dir.glob("*.jsonl"))
    if not files:
        pytest.skip("no travel jsonl files yet")
    # Sample one record per file (full validation would scan ~100K rows).
    for f in files:
        with open(f) as fp:
            line = fp.readline()
        if not line.strip():
            continue
        rec = json.loads(line)
        assert isinstance(rec.get("text"), str) and rec["text"].strip()
        meta = rec.get("meta") or {}
        assert meta.get("domain") == "travel", (
            f"{f.name}: meta.domain={meta.get('domain')!r}, expected 'travel'"
        )
