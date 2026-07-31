"""Tests for the per-tier verification gates in ``vllm_ec2_bench.verify``.

Each gate here corresponds to a measurement error this harness actually made,
so the tests are written around those concrete failure shapes: silently dropped
responses, replayed payloads, straggler-tail timing gaps, and preemption.
"""
from __future__ import annotations

import json

import pytest

from vllm_ec2_bench.verify import (
    check_completeness,
    count_responses,
    cross_check_throughput,
    scrape_vllm_metrics,
    verify_tier,
)


def _write_responses(
    directory, prompts: list[str], filename="responses.jsonl", key="input_payload"
) -> None:
    """Write LLMeter-shaped response records.

    ``key`` defaults to ``input_payload``, which is what
    ``llmeter.endpoints.base.InvocationResponse`` actually serialises.
    """
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / filename).open("w") as fh:
        for p in prompts:
            fh.write(
                json.dumps(
                    {
                        key: {
                            "messages": [
                                {"role": "system", "content": "sys"},
                                {"role": "user", "content": p},
                            ]
                        }
                    }
                )
                + "\n"
            )


class TestCountResponses:
    def test_counts_records_and_distinct_prompts(self, tmp_path) -> None:
        _write_responses(tmp_path / "load_test", [f"note-{i}" for i in range(10)])
        n, distinct = count_responses(tmp_path)
        assert (n, distinct) == (10, 10)

    def test_detects_replayed_payloads(self, tmp_path) -> None:
        """The 40,000-requests-from-49-prompts signature."""
        _write_responses(tmp_path / "load_test", ["same-note"] * 100)
        n, distinct = count_responses(tmp_path)
        assert n == 100
        assert distinct == 1, "replayed payloads must be visible as a low distinct count"

    def test_aggregates_across_multiple_files(self, tmp_path) -> None:
        _write_responses(tmp_path / "t1", ["a", "b"], "responses.jsonl")
        _write_responses(tmp_path / "t2", ["c"], "responses-2.jsonl")
        n, distinct = count_responses(tmp_path)
        assert (n, distinct) == (3, 3)

    def test_missing_directory_is_zero_not_an_error(self, tmp_path) -> None:
        assert count_responses(tmp_path / "nope") == (0, 0)

    def test_reads_llmeters_input_payload_field(self, tmp_path) -> None:
        """Regression: the field is ``input_payload``, not ``payload``.

        Validated against real run output — reading the wrong key silently
        returned 0 distinct prompts for every file, which would have disabled
        replay detection exactly when it mattered.
        """
        _write_responses(tmp_path / "t", ["a", "b", "c"], key="input_payload")
        assert count_responses(tmp_path) == (3, 3)

    def test_falls_back_to_flattened_input_prompt(self, tmp_path) -> None:
        d = tmp_path / "t"
        d.mkdir(parents=True)
        with (d / "responses.jsonl").open("w") as fh:
            for p in ("note-one", "note-two"):
                fh.write(json.dumps({"input_prompt": f"system preamble {p}"}) + "\n")
        assert count_responses(tmp_path) == (2, 2)

    def test_malformed_lines_are_skipped_but_counted(self, tmp_path) -> None:
        d = tmp_path / "load_test"
        d.mkdir(parents=True)
        (d / "responses.jsonl").write_text('{"bad json\n\n{"payload":{}}\n')
        n, _ = count_responses(tmp_path)
        assert n == 2, "blank lines skipped, malformed lines still counted as records"


class TestCheckCompleteness:
    def test_full_completeness_passes(self) -> None:
        ok, ratio = check_completeness(40_000, 40_000)
        assert ok and ratio == 1.0

    def test_the_nine_percent_loss_case_fails(self) -> None:
        """LLMeter reported failed_requests=0 for exactly this run."""
        ok, ratio = check_completeness(36_394, 40_000)
        assert not ok
        assert 0.90 < ratio < 0.92

    def test_boundary_at_threshold_passes(self) -> None:
        ok, _ = check_completeness(98, 100, minimum=0.98)
        assert ok

    def test_zero_expected_fails_loudly(self) -> None:
        ok, ratio = check_completeness(0, 0)
        assert not ok and ratio == 0.0


class TestCrossCheckThroughput:
    def test_agreeing_rates_pass(self) -> None:
        # 1M tokens in 60s = 1M tok/min, matching the reported rate.
        ok, divergence, wall_rate = cross_check_throughput(
            stats_tokens_per_min=1_000_000,
            total_tokens=1_000_000,
            wall_clock_s=60.0,
        )
        assert ok
        assert divergence < 0.01
        assert wall_rate == pytest.approx(1_000_000)

    def test_straggler_tail_shows_up_as_divergence(self) -> None:
        """Dense work in 480s, tail stretches the window to 900s."""
        ok, divergence, _ = cross_check_throughput(
            stats_tokens_per_min=1_000_000,
            total_tokens=8_000_000,
            wall_clock_s=900.0,
        )
        assert not ok
        assert divergence > 0.4

    def test_zero_wall_clock_is_invalid(self) -> None:
        ok, divergence, _ = cross_check_throughput(
            stats_tokens_per_min=1000, total_tokens=100, wall_clock_s=0.0
        )
        assert not ok and divergence == float("inf")


class TestScrapeVllmMetrics:
    def test_sums_counters_across_dp_replicas(self, monkeypatch) -> None:
        body = "\n".join(
            [
                "# HELP whatever",
                'vllm:prefix_cache_queries_total{engine="0"} 1000.0',
                'vllm:prefix_cache_queries_total{engine="1"} 1000.0',
                'vllm:prefix_cache_hits_total{engine="0"} 20.0',
                'vllm:prefix_cache_hits_total{engine="1"} 42.0',
                'vllm:num_preemptions_total{engine="0"} 7.0',
            ]
        )
        _patch_urlopen(monkeypatch, body)
        out = scrape_vllm_metrics("http://198.51.100.1:8000/v1", "key")
        assert out["prefix_cache_queries_total"] == 2000.0
        assert out["prefix_cache_hits_total"] == 62.0
        assert out["num_preemptions_total"] == 7.0
        assert out["prefix_cache_hit_rate"] == 62.0 / 2000.0

    def test_network_failure_is_reported_not_raised(self, monkeypatch) -> None:
        def boom(*_a, **_k):
            raise OSError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        out = scrape_vllm_metrics("http://198.51.100.1:8000/v1", "key")
        assert "error" in out
        assert "connection refused" in out["error"]


def _patch_urlopen(monkeypatch, body: str) -> None:
    class _Resp:
        def read(self):
            return body.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())


class TestVerifyTier:
    def _healthy_kwargs(self, tmp_path, n=100):
        _write_responses(tmp_path / "load_test", [f"n{i}" for i in range(n)])
        return {
            "concurrency": 800,
            "output_dir": tmp_path,
            "n_expected": n,
            "stats_tokens_per_min": 1_000_000,
            "total_tokens": 1_000_000,
            "wall_clock_s": 60.0,
        }

    def test_healthy_tier_is_valid(self, tmp_path) -> None:
        verdict = verify_tier(**self._healthy_kwargs(tmp_path))
        assert verdict.valid
        assert verdict.reasons == []
        assert verdict.completeness == 1.0
        assert verdict.distinct_prompts == 100

    def test_incomplete_tier_is_invalid_with_a_reason(self, tmp_path) -> None:
        kwargs = self._healthy_kwargs(tmp_path, n=100)
        kwargs["n_expected"] = 200
        verdict = verify_tier(**kwargs)
        assert not verdict.valid
        assert any("completeness" in r for r in verdict.reasons)

    def test_preemptions_invalidate_a_fast_looking_tier(self, tmp_path) -> None:
        """The c=2000 case: great throughput, 293 preemptions, unusable."""
        verdict = verify_tier(
            **self._healthy_kwargs(tmp_path),
            metrics_before={"num_preemptions_total": 0.0},
            metrics_after={"num_preemptions_total": 293.0},
        )
        assert not verdict.valid
        assert verdict.preemptions == 293.0
        assert any("preemption" in r for r in verdict.reasons)

    def test_zero_preemptions_stays_valid(self, tmp_path) -> None:
        verdict = verify_tier(
            **self._healthy_kwargs(tmp_path),
            metrics_before={"num_preemptions_total": 5.0},
            metrics_after={"num_preemptions_total": 5.0},
        )
        assert verdict.valid
        assert verdict.preemptions == 0.0

    def test_prefix_cache_hit_rate_computed_from_deltas(self, tmp_path) -> None:
        verdict = verify_tier(
            **self._healthy_kwargs(tmp_path),
            metrics_before={
                "prefix_cache_queries_total": 1000.0,
                "prefix_cache_hits_total": 900.0,
            },
            metrics_after={
                "prefix_cache_queries_total": 2000.0,
                "prefix_cache_hits_total": 931.0,
            },
        )
        # Deltas: 31 hits / 1000 queries = 3.1%, the real-workload figure.
        assert verdict.prefix_cache_hit_rate == 0.031

    def test_multiple_failures_are_all_reported(self, tmp_path) -> None:
        kwargs = self._healthy_kwargs(tmp_path, n=100)
        kwargs["n_expected"] = 500
        kwargs["wall_clock_s"] = 900.0
        verdict = verify_tier(
            **kwargs,
            metrics_before={"num_preemptions_total": 0.0},
            metrics_after={"num_preemptions_total": 50.0},
        )
        assert not verdict.valid
        assert len(verdict.reasons) == 3

    def test_as_dict_is_json_serialisable(self, tmp_path) -> None:
        verdict = verify_tier(**self._healthy_kwargs(tmp_path))
        json.dumps(verdict.as_dict())
