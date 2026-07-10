"""Async driver that bombards a local vLLM server with concurrent requests.

The driver is intentionally separated from the entrypoint so it can be
unit-tested against a fake vLLM HTTP server (or mocked httpx).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

LOG = logging.getLogger(__name__)


@dataclass
class InferenceResult:
    """Result of one (input record → vLLM → output) round-trip."""

    input_id: str | None
    """The ``id`` field from the input record, if present."""

    input_key: str
    """Stable identifier: '<source_uri>#<line_index>' or the id field."""

    request: dict[str, Any]
    response: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float | None = None
    attempts: int = 1

    # Throughput accounting — populated from response.usage if available.
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_jsonl_line(self) -> str:
        return json.dumps({
            "id": self.input_id,
            "input_key": self.input_key,
            "request": self.request,
            "response": self.response,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }, ensure_ascii=False)


@dataclass
class DriverStats:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    # Throughput accounting
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    started_at_monotonic: float | None = None
    """Filled in by drive_inference() right before firing the first request."""
    ended_at_monotonic: float | None = None
    """Filled in by drive_inference() after asyncio.gather returns."""

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.total if self.total else 0.0

    @property
    def p50_ms(self) -> float | None:
        return _percentile(self.latencies_ms, 50)

    @property
    def p99_ms(self) -> float | None:
        return _percentile(self.latencies_ms, 99)

    @property
    def wall_clock_s(self) -> float | None:
        """Wall-clock duration of the inference loop (not including vLLM
        startup). None if the loop hasn't finished."""
        if self.started_at_monotonic is None or self.ended_at_monotonic is None:
            return None
        return round(self.ended_at_monotonic - self.started_at_monotonic, 3)

    @property
    def input_tokens_per_second(self) -> float | None:
        wc = self.wall_clock_s
        if wc is None or wc <= 0:
            return None
        return round(self.total_input_tokens / wc, 2)

    @property
    def output_tokens_per_second(self) -> float | None:
        wc = self.wall_clock_s
        if wc is None or wc <= 0:
            return None
        return round(self.total_output_tokens / wc, 2)

    @property
    def total_tokens_per_second(self) -> float | None:
        wc = self.wall_clock_s
        if wc is None or wc <= 0:
            return None
        total = self.total_input_tokens + self.total_output_tokens
        return round(total / wc, 2)

    @property
    def requests_per_second(self) -> float | None:
        wc = self.wall_clock_s
        if wc is None or wc <= 0:
            return None
        return round(self.succeeded / wc, 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "success_rate": round(self.success_rate, 4),
            "p50_ms": self.p50_ms,
            "p99_ms": self.p99_ms,
            # Throughput
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "wall_clock_s": self.wall_clock_s,
            "input_tokens_per_second": self.input_tokens_per_second,
            "output_tokens_per_second": self.output_tokens_per_second,
            "total_tokens_per_second": self.total_tokens_per_second,
            "requests_per_second": self.requests_per_second,
        }


def _percentile(values: list[float], p: float) -> float | None:
    """LLMeter-compatible percentile (matches ``statistics.quantiles``)."""
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    from statistics import StatisticsError, median, quantiles
    try:
        if p == 50:
            return round(median(values), 2)
        for k in (4, 10, 100):
            if p % (100 / k) == 0:
                qs = quantiles(values, n=k)
                return round(qs[int(p * k / 100) - 1], 2)
    except StatisticsError:
        return None
    raise ValueError(f"Unsupported percentile {p}; must be 1-99 integer.")


async def drive_inference(
    records: list[tuple[str, dict[str, Any]]],
    *,
    vllm_base_url: str,
    in_flight: int,
    request_timeout_s: float = 120.0,
    per_request_max_retries: int = 2,
) -> tuple[list[InferenceResult], DriverStats]:
    """Run each request through vLLM with bounded in-flight concurrency.

    Parameters
    ----------
    records
        List of ``(input_key, request_body)`` tuples. ``input_key`` is
        opaque but should uniquely identify the record (URI + line index
        or user-provided id).
    vllm_base_url
        e.g. ``http://localhost:8000`` — OpenAI-compatible prefix.
    in_flight
        Maximum concurrent in-flight requests.
    request_timeout_s
        Per-request httpx timeout.
    per_request_max_retries
        Retry 5xx + network errors this many times, then give up.

    Returns
    -------
    (results, stats) — results is in the same order as ``records``.
    """
    sem = asyncio.Semaphore(in_flight)
    stats = DriverStats(total=len(records))
    results: list[InferenceResult | None] = [None] * len(records)

    async with httpx.AsyncClient(
        base_url=vllm_base_url,
        timeout=request_timeout_s,
        limits=httpx.Limits(max_connections=in_flight * 2),
    ) as client:

        async def one(idx: int, input_key: str, request: dict[str, Any]) -> None:
            input_id = request.get("id") if isinstance(request.get("id"), (str, int)) else None
            # vLLM doesn't want an 'id' field at the top level of a
            # ChatCompletions request, so strip it if present.
            vllm_request = {k: v for k, v in request.items() if k != "id"}

            async with sem:
                t0 = time.monotonic()
                err: str | None = None
                resp: dict[str, Any] | None = None
                attempts = 0
                try:
                    async for attempt in AsyncRetrying(
                        stop=stop_after_attempt(per_request_max_retries + 1),
                        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
                        retry=retry_if_exception_type((httpx.HTTPError,)),
                        reraise=True,
                    ):
                        with attempt:
                            attempts = attempt.retry_state.attempt_number
                            r = await client.post(
                                "/v1/chat/completions", json=vllm_request,
                            )
                            if r.status_code >= 500:
                                raise httpx.HTTPStatusError(
                                    f"server {r.status_code}",
                                    request=r.request, response=r,
                                )
                            if r.status_code >= 400:
                                # 4xx is user error — don't retry
                                err = f"HTTP {r.status_code}: {r.text[:300]}"
                                break
                            resp = r.json()
                except httpx.HTTPError as exc:
                    err = f"{type(exc).__name__}: {exc}"
                except Exception as exc:  # noqa: BLE001
                    err = f"{type(exc).__name__}: {exc}"
                latency_ms = round((time.monotonic() - t0) * 1000, 2)

                input_tokens: int | None = None
                output_tokens: int | None = None
                if err is None and resp is not None:
                    stats.succeeded += 1
                    stats.latencies_ms.append(latency_ms)
                    # vLLM / OpenAI API returns usage.prompt_tokens +
                    # usage.completion_tokens. Accept both OpenAI
                    # variants. Missing usage is tolerated (some forks
                    # don't emit it); we simply don't count tokens for
                    # that request.
                    usage = resp.get("usage") if isinstance(resp, dict) else None
                    if isinstance(usage, dict):
                        in_t = usage.get("prompt_tokens") or usage.get("input_tokens")
                        out_t = usage.get("completion_tokens") or usage.get("output_tokens")
                        if isinstance(in_t, int):
                            input_tokens = in_t
                            stats.total_input_tokens += in_t
                        if isinstance(out_t, int):
                            output_tokens = out_t
                            stats.total_output_tokens += out_t
                else:
                    stats.failed += 1

                result = InferenceResult(
                    input_id=str(input_id) if input_id is not None else None,
                    input_key=str(input_id) if input_id is not None else input_key,
                    request=vllm_request,
                    response=resp,
                    error=err,
                    latency_ms=latency_ms,
                    attempts=attempts or 1,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                results[idx] = result

        stats.started_at_monotonic = time.monotonic()
        await asyncio.gather(*[
            one(i, k, r) for i, (k, r) in enumerate(records)
        ])
        stats.ended_at_monotonic = time.monotonic()

    # results is fully populated by construction; mypy doesn't know
    return [r for r in results if r is not None], stats


async def wait_for_vllm_ready(
    base_url: str, *, timeout_s: float = 900.0, interval_s: float = 5.0,
) -> None:
    """Poll ``/v1/models`` until it returns 200, or raise on timeout."""
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get("/v1/models")
                if r.status_code == 200:
                    LOG.info("vLLM is ready (/v1/models → 200)")
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(interval_s)
    raise TimeoutError(
        f"vLLM at {base_url} did not become ready within {timeout_s}s"
    )
