"""Tests for UniquePayloadEndpoint and make_http_client.

These guard the measurement-correctness fix described in
``vllm_ec2_bench.endpoint.vllm_openai``: LLMeter's constant-seeded payload
shuffle makes every client replay the same inputs, which turns
``--enable-prefix-caching`` into a throughput inflator.
"""
from __future__ import annotations

import threading

import pytest

from vllm_ec2_bench.endpoint import (
    PayloadPoolExhausted,
    UniquePayloadEndpoint,
    make_http_client,
)

SYSTEM = "You are a structured-data extractor."


def _endpoint(inputs: list[str], **kwargs) -> UniquePayloadEndpoint:
    return UniquePayloadEndpoint(
        base_url="http://198.51.100.1:8000/v1",
        api_key="test-key",
        model_id="test-model",
        inputs=inputs,
        system_prompt=SYSTEM,
        **kwargs,
    )


class TestUniquePayloadEndpoint:
    def test_every_request_gets_a_distinct_input(self) -> None:
        inputs = [f"note-{i}" for i in range(50)]
        ep = _endpoint(inputs)
        seen = [
            ep.prepare_payload({})["messages"][1]["content"] for _ in range(50)
        ]
        assert len(set(seen)) == 50, "each request must carry a distinct input"
        assert set(seen) == set(inputs)

    def test_system_prompt_is_the_only_shared_prefix(self) -> None:
        ep = _endpoint(["a", "b"])
        first = ep.prepare_payload({})
        second = ep.prepare_payload({})
        assert first["messages"][0] == {"role": "system", "content": SYSTEM}
        assert second["messages"][0] == {"role": "system", "content": SYSTEM}
        assert first["messages"][1]["content"] != second["messages"][1]["content"]

    def test_sampling_params_are_applied(self) -> None:
        ep = _endpoint(["a"], max_tokens=1024, temperature=0.0, top_p=1.0)
        payload = ep.prepare_payload({})
        assert payload["max_tokens"] == 1024
        assert payload["temperature"] == 0.0
        assert payload["top_p"] == 1.0

    def test_pool_exhaustion_raises_rather_than_recycling(self) -> None:
        """Recycling silently is the bug; failing loudly is the fix."""
        ep = _endpoint(["only-one"])
        ep.prepare_payload({})
        with pytest.raises(PayloadPoolExhausted, match="exhausted"):
            ep.prepare_payload({})

    def test_reset_pool_allows_reuse_between_tiers(self) -> None:
        ep = _endpoint(["a", "b", "c"])
        for _ in range(3):
            ep.prepare_payload({})
        assert ep.remaining() == 0
        ep.reset_pool()
        assert ep.remaining() == 3
        assert ep.served == 0
        assert ep.prepare_payload({})["messages"][1]["content"] == "a"

    def test_reset_pool_with_offset(self) -> None:
        ep = _endpoint(["a", "b", "c"])
        ep.reset_pool(offset=2)
        assert ep.prepare_payload({})["messages"][1]["content"] == "c"

    def test_served_and_remaining_track_consumption(self) -> None:
        ep = _endpoint([f"n{i}" for i in range(10)])
        assert (ep.served, ep.remaining()) == (0, 10)
        ep.prepare_payload({})
        ep.prepare_payload({})
        assert (ep.served, ep.remaining()) == (2, 8)

    def test_empty_pool_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            _endpoint([])

    def test_thread_safe_under_concurrent_consumption(self) -> None:
        """LLMeter drives clients via asyncio.to_thread, so this is the real path."""
        n = 400
        ep = _endpoint([f"note-{i}" for i in range(n)])
        collected: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            local = []
            for _ in range(n // 8):
                local.append(ep.prepare_payload({})["messages"][1]["content"])
            with lock:
                collected.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(collected) == n
        assert len(set(collected)) == n, "concurrent pops must not hand out duplicates"


class TestMakeHttpClient:
    def test_lifts_connection_limit_above_the_sdk_default(self) -> None:
        client = make_http_client(4096)
        try:
            limits = client._transport._pool._max_connections
            assert limits == 4096, "must exceed the OpenAI SDK's 1000 default"
        finally:
            client.close()

    def test_default_is_large_enough_for_observed_sweeps(self) -> None:
        client = make_http_client()
        try:
            assert client._transport._pool._max_connections >= 2000
        finally:
            client.close()

    def test_timeout_is_configurable(self) -> None:
        client = make_http_client(64, timeout_s=123.0)
        try:
            assert client.timeout.read == 123.0
        finally:
            client.close()
