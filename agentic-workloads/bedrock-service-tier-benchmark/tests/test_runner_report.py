"""Tests for the runner, adapters, catalog parser and reports (no AWS calls)."""

from __future__ import annotations

import json

from bedrock_bench import catalog
from bedrock_bench.apis.base import Measurement, Request, Usage
from bedrock_bench.apis.bedrock_native import ConverseStream, InvokeStream, invoke_body
from bedrock_bench.benchmark import estimate
from bedrock_bench.cells import expand_cells
from bedrock_bench.config import BenchmarkConfig, CacheMode
from bedrock_bench.html_report import render
from bedrock_bench.metrics import summarize
from bedrock_bench.registry import spec_from_dict
from bedrock_bench.report import compare, redact_meta, redact_text, write_markdown
from bedrock_bench.runner import Runner, classify_sample, served_matches

SPEC = spec_from_dict(
    {
        "key": "vendor.m",
        "display_name": "M",
        "cache": {"implicit": True, "explicit": True, "min_tokens": 1024},
        "offerings": [
            {
                "endpoint": "runtime",
                "api": "converse_stream",
                "scope": "geo",
                "model_id": "us.vendor.m",
                "regions": ["us-east-1"],
                "tiers": ["default", "flex"],
            }
        ],
    }
)


class FakeAdapter:
    def __init__(self, cell):
        self.cell = cell
        self.docs: list[str] = []

    def send(self, req: Request) -> Measurement:
        self.docs.append(req.document)
        warm = self.cell.cache.is_warm
        return Measurement(
            ttft=0.5 if req.tier is None else 2.0,
            ttfat=0.5 if req.tier is None else 2.0,
            e2e=1.0 if req.tier is None else 2.1,
            served_tier=req.tier or "default",
            usage=Usage(output_tokens=50, cache_read_tokens=900 if warm else 0),
        )


def _run(cache_modes=("cold",), n=3):
    cfg = BenchmarkConfig(
        apis=("converse_stream",),
        cache_modes=cache_modes,
        tiers=("default", "flex"),
        n_requests=n,
        interval_seconds=0,
        warmup_requests=1,
    )
    cells = expand_cells(cfg, [SPEC])
    adapters = {}

    def factory(cell, _broker):
        adapters[cell.label] = FakeAdapter(cell)
        return adapters[cell.label]

    records = Runner(cfg, broker=None, adapter_factory=factory, sleep=lambda s: None).run(cells)
    return cfg, cells, records, adapters


# --------------------------------------------------------------------------- runner
def test_runner_discards_warmup_and_includes_clean_samples():
    _, cells, records, _ = _run()
    assert len(cells) == 2
    for c in cells:
        assert len(records[c.label]) == 3  # warm-up discarded
        assert all(r["included"] for r in records[c.label])


def test_warm_contexts_share_prefix_across_tiers():
    _, cells, _, adapters = _run(cache_modes=("warm_implicit",))
    docs = {d for a in adapters.values() for d in a.docs}
    assert len(docs) == 1  # one shared document for the whole context


def test_classify_sample_rules():
    _, cells, _, _ = _run()
    cold = cells[0]
    hit = Measurement(ttft=1, e2e=2, usage=Usage(cache_read_tokens=10))
    assert classify_sample(cold, hit) == (False, "cache_contaminated")
    wrong = Measurement(ttft=1, e2e=2, served_tier="flex", usage=Usage(cache_read_tokens=0))
    default_cell = next(c for c in cells if c.tier.is_default)
    assert classify_sample(default_cell, wrong) == (False, "tier_mismatch")
    assert served_matches(default_cell.tier, "standard") is True
    assert served_matches(default_cell.tier, None) is None


def test_compare_and_reports_render():
    cfg, cells, records, _ = _run(n=5)
    summaries = [summarize({**c.dims(), "label": c.label}, records[c.label]) for c in cells]
    comps = compare(summaries, records, seed=1)
    assert len(comps) == 1 and comps[0]["tier"] == "flex"
    assert comps[0]["deltas"]["e2e"]["delta"] > 1.0
    html = render(
        {"meta": {"run_id": "r"}, "cells": [s.to_dict() for s in summaries], "comparisons": comps}
    )
    assert '<script id="data" type="application/json">' in html
    assert "</script><" not in html.split('type="application/json">', 1)[1].split("</script>")[0]


def test_markdown_labels_do_not_break_table(tmp_path):
    cfg, cells, records, _ = _run()
    summaries = [summarize({**c.dims(), "label": c.label}, records[c.label]) for c in cells]
    out = tmp_path / "r.md"
    write_markdown(out, {"run_id": "r"}, summaries, compare(summaries, records, seed=1))
    row = next(line for line in out.read_text().splitlines() if line.startswith("| vendor.m"))
    assert row.count("|") == 11  # 10 columns


def test_redaction():
    msg = "User: arn:aws:sts::123456789012:assumed-role/x is not authorized (123456789012)"
    assert "123456789012" not in redact_text(msg)
    meta = redact_meta(
        {"account_id": "123456789012", "profile": "p", "preflight": [{"error": msg}]}
    )
    assert "profile" not in meta and "123456789012" not in json.dumps(meta)


def test_html_escapes_script_breakout():
    html = render(
        {"meta": {"run_id": "</script><script>alert(1)</script>"}, "cells": [], "comparisons": []}
    )
    assert "<script>alert(1)" not in html


# --------------------------------------------------------------------------- adapters
class FakeConverseClient:
    def converse_stream(self, **kw):
        assert kw["serviceTier"] == {"type": "flex"}
        return {
            "stream": [
                {"messageStart": {}},
                {"contentBlockDelta": {"delta": {"reasoningContent": {"text": "hmm"}}}},
                {"contentBlockDelta": {"delta": {"text": "Hi"}}},
                {"messageStop": {"stopReason": "end_turn"}},
                {
                    "metadata": {
                        "usage": {"inputTokens": 5, "outputTokens": 3, "cacheReadInputTokens": 100},
                        "serviceTier": {"type": "flex"},
                    }
                },
            ]
        }


def test_converse_stream_adapter_parses_stream():
    m = ConverseStream(FakeConverseClient()).send(
        Request("us.m", "us-east-1", "doc", "q", 10, "flex", explicit_cache=True)
    )
    assert m.error is None and m.served_tier == "flex"
    assert m.ttft is not None and m.ttfat is not None and m.ttft <= m.ttfat
    assert m.usage.cache_read_tokens == 100 and m.usage.input_tokens == 105


class FakeInvokeClient:
    def invoke_model_with_response_stream(self, **kw):
        chunks = [
            {"choices": [{"delta": {"content": "", "role": "assistant"}}]},
            {"choices": [{"delta": {"content": "Hello"}}], "service_tier": "default"},
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 2,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
                "amazon-bedrock-invocationMetrics": {"firstByteLatency": 1200},
            },
        ]
        return {
            "body": [{"chunk": {"bytes": json.dumps(c).encode()}} for c in chunks],
            "ResponseMetadata": {"HTTPHeaders": {}},
        }


def test_invoke_stream_skips_empty_first_chunk_and_reads_server_latency():
    m = InvokeStream(FakeInvokeClient()).send(Request("m", "us-east-1", "d", "q", 10, None))
    assert m.error is None and m.ttft is not None
    assert m.server_first_byte == 1.2 and m.usage.output_tokens == 2


def test_invoke_body_explicit_cache_marks_document():
    body = invoke_body(
        Request("m", "r", "DOC", "Q", 5, None, explicit_cache=True), "openai", stream=True
    )
    assert body["messages"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert body["prompt_cache_options"]["mode"] == "explicit"


# --------------------------------------------------------------------------- catalog
CARD = """<html><head><title>GLM 9 - Amazon Bedrock</title></head><body>
<h2>Model Details</h2><table>
<tr><th>Input Modalities</th><th>Output Modalities</th><th>APIs supported</th><th>Endpoints supported</th></tr>
<tr><td><img alt="Green circle with white checkmark icon."/> Text</td><td><img alt="Green circle with white checkmark icon."/> Text</td>
<td><img alt="Green circle with white checkmark icon."/> Converse</td><td><img alt="Green circle with white checkmark icon."/> bedrock-runtime</td></tr>
</table>
<h2>Programmatic Access</h2><table>
<tr><th>Endpoint</th><th>Model ID</th><th>Geo inference ID</th><th>Global inference ID</th></tr>
<tr><td>bedrock-runtime</td><td>zai.glm-9</td><td>us.zai.glm-9</td><td>global.zai.glm-9</td></tr></table>
<h2>Service Tiers</h2><table>
<tr><th>Standard</th><th>Priority</th><th>Flex</th><th>Reserved</th></tr>
<tr><td><img alt="Green circle with white checkmark icon."/></td><td><img alt="Red circle with white X icon"/></td>
<td><img alt="Green circle with white checkmark icon."/></td><td><img alt="Red circle with white X icon"/></td></tr>
<tr><th>Region</th><th>In-Region</th><th>Geo</th><th>Global</th></tr>
<tr><td>us-east-1 (N. Virginia)</td><td><img alt="Red circle with white X icon"/></td>
<td><img alt="Green circle with white checkmark icon."/></td><td><img alt="Green circle with white checkmark icon."/></td></tr>
</table></body></html>"""


def test_model_card_parser():
    card = catalog.parse_model_card(CARD, "https://docs.aws.amazon.com/bedrock/x.html")
    assert card.title == "GLM 9"
    assert card.apis == ["Converse"] and card.endpoints == ["bedrock-runtime"]
    assert card.tiers == {"Standard": True, "Priority": False, "Flex": True, "Reserved": False}
    assert card.regions["us-east-1"] == {"In-Region": False, "Geo": True, "Global": True}
    assert card.access[0]["geo_ids"] == ["us.zai.glm-9"]


def test_fetch_refuses_other_hosts():
    import pytest

    for url in (
        "http://docs.aws.amazon.com/bedrock/x",
        "https://evil.example/bedrock/x",
        "https://docs.aws.amazon.com/other/x",
    ):
        with pytest.raises(ValueError):
            catalog.fetch(url)


def test_cache_mode_enum_round_trip():
    assert CacheMode("warm_explicit").is_warm


# --------------------------------------------------------------------------- review r2 fixes
def test_unreported_tier_is_excluded_for_non_default():
    _, cells, _, _ = _run()
    flex = next(c for c in cells if not c.tier.is_default)
    default = next(c for c in cells if c.tier.is_default)
    m = Measurement(ttft=1, e2e=2, served_tier=None, usage=Usage())
    assert classify_sample(flex, m) == (False, "tier_unreported")
    assert classify_sample(default, m) == (True, None)


def test_non_streaming_overrun_is_a_timeout():
    import time as _t

    from bedrock_bench.apis.base import StreamRecorder

    rec = StreamRecorder()
    rec.start(timeout=0.01)
    _t.sleep(0.02)
    import pytest

    with pytest.raises(TimeoutError):
        rec.finish()


def test_adapter_build_failure_records_errors_and_continues():
    cfg = BenchmarkConfig(
        apis=("converse_stream",),
        tiers=("default", "flex"),
        n_requests=2,
        interval_seconds=0,
    )
    cells = expand_cells(cfg, [SPEC])

    def boom(cell, _broker):
        raise RuntimeError("token mint failed")

    records = Runner(cfg, broker=None, adapter_factory=boom, sleep=lambda s: None).run(cells)
    assert all(len(records[c.label]) == 2 for c in cells)
    assert all(
        r["error_kind"] == "client" and not r["included"] for c in cells for r in records[c.label]
    )


def test_estimate_counts_warmup_per_context():
    cfg = BenchmarkConfig(
        apis=("converse_stream",),
        cache_modes=("cold", "warm_implicit"),
        tiers=("default", "flex"),
        n_requests=5,
        warmup_requests=0,
        interval_seconds=1,
    )
    cells = expand_cells(cfg, [SPEC])
    cold = [c for c in cells if not c.cache.is_warm]
    warm = [c for c in cells if c.cache.is_warm]
    assert estimate(cfg, cells)["requests"] == 5 * len(cold) + 6 * len(warm)


def test_discovery_refuses_lossy_overwrite():
    from dataclasses import replace as _replace

    from bedrock_bench.discovery import overwrite_refusal

    probed = _replace(SPEC, verified_at="2026-10-08")
    other = _replace(SPEC, key="vendor.n")
    assert overwrite_refusal([], [SPEC]) == "no models discovered"
    assert "shrink" in overwrite_refusal([SPEC], [SPEC, other])
    assert "documentation-only" in overwrite_refusal([SPEC], [probed])
    assert overwrite_refusal([probed, other], [probed]) is None
