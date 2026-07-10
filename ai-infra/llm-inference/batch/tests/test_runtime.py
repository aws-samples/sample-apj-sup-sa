"""Tests for the runtime — driver against a mocked vLLM, s3_io parsing."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from llm_batch_deploy.runtime.s3_io import S3Uri, iter_input_records
from llm_batch_deploy.runtime.vllm_driver import (
    DriverStats,
    drive_inference,
    wait_for_vllm_ready,
)


# ---------------------------------------------------------------------------
# S3Uri
# ---------------------------------------------------------------------------
class TestS3Uri:
    def test_parse(self) -> None:
        u = S3Uri.parse("s3://my-bucket/prefix/foo.json")
        assert u.bucket == "my-bucket"
        assert u.key == "prefix/foo.json"

    def test_parse_rejects_non_s3(self) -> None:
        with pytest.raises(ValueError, match="Not an s3"):
            S3Uri.parse("https://example.com/foo")

    def test_parse_rejects_malformed(self) -> None:
        with pytest.raises(ValueError, match="Malformed"):
            S3Uri.parse("s3://bucket-only")

    def test_join(self) -> None:
        base = S3Uri("my-bucket", "out/job-1")
        sub = base.join("shard-0", "result.jsonl")
        assert str(sub) == "s3://my-bucket/out/job-1/shard-0/result.jsonl"

    def test_join_handles_trailing_slash(self) -> None:
        base = S3Uri("b", "out/")
        assert str(base.join("x.json")) == "s3://b/out/x.json"

    def test_str(self) -> None:
        u = S3Uri("b", "k")
        assert str(u) == "s3://b/k"


# ---------------------------------------------------------------------------
# iter_input_records — accepts .json AND .jsonl shapes
# ---------------------------------------------------------------------------
class TestIterInputRecords:
    def test_jsonl(self) -> None:
        body = '{"id": 1, "a": 1}\n{"id": 2, "a": 2}\n\n{"id": 3}\n'
        rs = iter_input_records(body, uri="s3://b/foo.jsonl")
        assert len(rs) == 3
        assert [r["id"] for r in rs] == [1, 2, 3]

    def test_single_object(self) -> None:
        body = '{"id": 42, "prompt": "hello"}'
        rs = iter_input_records(body, uri="s3://b/foo.json")
        assert len(rs) == 1
        assert rs[0]["id"] == 42

    def test_json_array(self) -> None:
        body = '[{"id": 1}, {"id": 2}, {"id": 3}]'
        rs = iter_input_records(body, uri="s3://b/foo.json")
        assert [r["id"] for r in rs] == [1, 2, 3]

    def test_empty(self) -> None:
        assert iter_input_records("", uri="s3://b/empty.json") == []
        assert iter_input_records("\n\n  \n", uri="s3://b/empty.jsonl") == []

    def test_array_with_non_object_rejected(self) -> None:
        with pytest.raises(ValueError, match="not an object"):
            iter_input_records('[{"ok": 1}, "string"]', uri="s3://b/bad.json")

    def test_invalid_jsonl_line(self) -> None:
        with pytest.raises(ValueError, match="invalid JSON"):
            iter_input_records(
                '{"ok": 1}\nnot json\n', uri="s3://b/bad.jsonl",
            )

    def test_jsonl_with_non_object_rejected(self) -> None:
        with pytest.raises(ValueError, match="expected object"):
            iter_input_records('"string line"\n', uri="s3://b/bad.jsonl")


# ---------------------------------------------------------------------------
# drive_inference against a fake vLLM via httpx.MockTransport
# ---------------------------------------------------------------------------
def _mock_vllm(response_body: dict | None = None, *, fail_every: int | None = None):
    """Create an httpx.MockTransport that mimics vLLM's ChatCompletions.

    If ``fail_every`` is given, every N-th request returns 500 once then
    succeeds on retry (triggers the tenacity retry path).
    """
    calls = {"n": 0, "fail_counts": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "fake"}]})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            call_key = body.get("messages", [{}])[0].get("content", "x")
            if fail_every and calls["n"] % fail_every == 0:
                fc = calls["fail_counts"].get(call_key, 0)
                calls["fail_counts"][call_key] = fc + 1
                if fc == 0:
                    # First attempt for this call → fail once
                    return httpx.Response(500, text="transient")
            resp = response_body or {
                "id": "fake-resp", "model": body.get("model", "?"),
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"total_tokens": 10},
            }
            return httpx.Response(200, json=resp)
        return httpx.Response(404)

    return httpx.MockTransport(handler), calls


@pytest.fixture
def patch_httpx(monkeypatch):
    """Replace httpx.AsyncClient with one that uses MockTransport.

    Each test calls ``patch_httpx(transport)`` to install.
    """
    def _install(transport: httpx.MockTransport):
        real_client_cls = httpx.AsyncClient

        class _MockedClient(real_client_cls):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        monkeypatch.setattr("llm_batch_deploy.runtime.vllm_driver.httpx.AsyncClient", _MockedClient)
    return _install


class TestDriveInference:
    @pytest.mark.asyncio
    async def test_happy_path(self, patch_httpx) -> None:
        transport, calls = _mock_vllm()
        patch_httpx(transport)

        records = [
            (f"key-{i}", {"model": "fake", "messages": [{"role": "user", "content": f"hi-{i}"}]})
            for i in range(10)
        ]
        results, stats = await drive_inference(
            records, vllm_base_url="http://vllm", in_flight=4,
        )
        assert stats.total == 10
        assert stats.succeeded == 10
        assert stats.failed == 0
        assert all(r.response is not None for r in results)
        assert all(r.error is None for r in results)
        assert all(r.latency_ms is not None for r in results)

    @pytest.mark.asyncio
    async def test_retry_on_5xx(self, patch_httpx) -> None:
        transport, calls = _mock_vllm(fail_every=2)  # half the calls get a transient 500
        patch_httpx(transport)

        records = [
            (f"key-{i}", {"model": "fake", "messages": [{"role": "user", "content": f"hi-{i}"}]})
            for i in range(4)
        ]
        results, stats = await drive_inference(
            records, vllm_base_url="http://vllm", in_flight=2,
            per_request_max_retries=2,
        )
        # All should ultimately succeed after the retry.
        assert stats.succeeded == 4
        assert any(r.attempts > 1 for r in results), "Expected at least one retry"

    @pytest.mark.asyncio
    async def test_4xx_not_retried(self, patch_httpx, monkeypatch) -> None:
        # Custom handler that always returns 400
        def h(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/chat/completions":
                return httpx.Response(400, text="bad request")
            return httpx.Response(404)
        patch_httpx(httpx.MockTransport(h))

        records = [("k", {"model": "f", "messages": [{"role": "user", "content": "x"}]})]
        results, stats = await drive_inference(
            records, vllm_base_url="http://vllm", in_flight=1,
            per_request_max_retries=3,
        )
        assert stats.failed == 1
        assert results[0].error is not None and "400" in results[0].error
        assert results[0].attempts == 1, "4xx must not be retried"

    @pytest.mark.asyncio
    async def test_id_field_propagates(self, patch_httpx) -> None:
        transport, _ = _mock_vllm()
        patch_httpx(transport)
        records = [
            ("fallback-key", {"id": "my-explicit-id", "model": "f", "messages": [{"role": "user", "content": "x"}]}),
        ]
        results, _ = await drive_inference(
            records, vllm_base_url="http://vllm", in_flight=1,
        )
        assert results[0].input_id == "my-explicit-id"
        assert results[0].input_key == "my-explicit-id"
        # 'id' should be stripped from the request that went to vLLM
        assert "id" not in results[0].request

    @pytest.mark.asyncio
    async def test_usage_tokens_recorded(self, patch_httpx) -> None:
        """vLLM/OpenAI response.usage values must flow into the result
        + aggregate into DriverStats totals for throughput calculation."""
        def handler(request):
            import httpx as _httpx
            if request.url.path == "/v1/models":
                return _httpx.Response(200, json={"data": []})
            body = json.loads(request.content)
            return _httpx.Response(200, json={
                "id": "fake", "model": body.get("model", "?"),
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 10,
                          "total_tokens": 35},
            })
        import httpx as _httpx
        patch_httpx(_httpx.MockTransport(handler))

        records = [
            (f"k-{i}", {"model": "f", "messages": [
                {"role": "user", "content": "x"}]})
            for i in range(4)
        ]
        results, stats = await drive_inference(
            records, vllm_base_url="http://vllm", in_flight=2,
        )
        # Each result carries its own counts
        assert all(r.input_tokens == 25 for r in results)
        assert all(r.output_tokens == 10 for r in results)
        # Stats aggregate
        assert stats.total_input_tokens == 100
        assert stats.total_output_tokens == 40
        # Wall-clock populated (monotonic, just check > 0)
        assert stats.wall_clock_s is not None and stats.wall_clock_s > 0
        # Throughput computable
        assert stats.total_tokens_per_second is not None
        assert stats.total_tokens_per_second > 0


class TestRequestTimeoutPlumbing:
    """Audit-shape regression: ``request_timeout_s`` must reach the httpx
    AsyncClient untouched. Regressions here would silently revert per-request
    timeouts to the framework default (120s), undoing per-plan opt-ups."""

    @pytest.mark.asyncio
    async def test_drive_inference_passes_request_timeout_to_httpx(
        self, monkeypatch
    ) -> None:
        captured: dict[str, object] = {}

        real_client_cls = httpx.AsyncClient

        class _Capture(real_client_cls):
            def __init__(self, *args, **kwargs):
                captured["timeout"] = kwargs.get("timeout")
                # Route all requests to a benign mock.
                kwargs["transport"] = _mock_vllm()[0]
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(
            "llm_batch_deploy.runtime.vllm_driver.httpx.AsyncClient",
            _Capture,
        )

        records = [
            ("k", {"model": "f", "messages": [{"role": "user", "content": "x"}]}),
        ]
        await drive_inference(
            records, vllm_base_url="http://vllm", in_flight=1,
            request_timeout_s=600.0,
        )
        assert captured["timeout"] == 600.0, (
            "drive_inference must thread request_timeout_s through to "
            "httpx.AsyncClient(timeout=...) — caught a regression where the "
            "kwarg was dropped"
        )


class TestDriverStats:
    def test_percentiles(self) -> None:
        # LLMeter-compatible percentiles via statistics.median + quantiles:
        #   latencies = [10, 20, 30, 40, 50]
        #   p50 = median([10,20,30,40,50]) = 30
        #   p99 = quantiles(data, n=100)[98] = 59.4 (exclusive method
        #         extrapolates for small N; see Python statistics docs)
        s = DriverStats(total=5, succeeded=5, failed=0,
                        latencies_ms=[10, 20, 30, 40, 50])
        assert s.p50_ms == 30
        assert s.p99_ms == 59.4
        assert s.success_rate == 1.0

    def test_empty(self) -> None:
        s = DriverStats()
        assert s.p50_ms is None
        assert s.success_rate == 0.0
        # Throughput properties are None without wall-clock / tokens
        assert s.wall_clock_s is None
        assert s.input_tokens_per_second is None
        assert s.total_tokens_per_second is None
        assert s.requests_per_second is None

    def test_throughput_with_wall_clock(self) -> None:
        s = DriverStats(
            total=10, succeeded=10, failed=0,
            total_input_tokens=2_000, total_output_tokens=1_000,
            started_at_monotonic=100.0, ended_at_monotonic=110.0,  # 10s
        )
        assert s.wall_clock_s == 10.0
        assert s.input_tokens_per_second == 200.0
        assert s.output_tokens_per_second == 100.0
        assert s.total_tokens_per_second == 300.0
        assert s.requests_per_second == 1.0

    def test_as_dict_includes_throughput(self) -> None:
        s = DriverStats(
            total=5, succeeded=5, failed=0,
            total_input_tokens=500, total_output_tokens=250,
            started_at_monotonic=0.0, ended_at_monotonic=5.0,
        )
        d = s.as_dict()
        for key in (
            "total_input_tokens", "total_output_tokens", "wall_clock_s",
            "input_tokens_per_second", "output_tokens_per_second",
            "total_tokens_per_second", "requests_per_second",
        ):
            assert key in d, f"{key} missing from as_dict()"
        assert d["total_tokens_per_second"] == 150.0


class TestWaitForVllmReady:
    @pytest.mark.asyncio
    async def test_ready_immediately(self, patch_httpx) -> None:
        transport, _ = _mock_vllm()
        patch_httpx(transport)
        await wait_for_vllm_ready("http://vllm", timeout_s=2.0, interval_s=0.1)

    @pytest.mark.asyncio
    async def test_timeout(self, patch_httpx) -> None:
        def h(r: httpx.Request) -> httpx.Response:
            return httpx.Response(503)
        patch_httpx(httpx.MockTransport(h))
        with pytest.raises(TimeoutError):
            await wait_for_vllm_ready("http://vllm", timeout_s=0.3, interval_s=0.1)
