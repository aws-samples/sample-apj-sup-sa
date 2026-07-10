"""Tests for the in-memory CircuitBreaker."""

from __future__ import annotations

from gateway.domains.runtime.circuit_breaker import BreakerState, CircuitBreaker


def _fixed_clock(seconds: list[float]):
    """Returns a callable that yields successive values from `seconds`."""

    iterator = iter(seconds)

    def _now() -> float:
        return next(iterator)

    return _now


def test_initial_state_is_closed_and_allows_bedrock() -> None:
    breaker = CircuitBreaker(open_seconds=300.0)
    assert breaker.state("ap-northeast-2") == BreakerState.CLOSED
    assert breaker.allow_bedrock("ap-northeast-2") is True


def test_record_failure_transitions_to_open() -> None:
    breaker = CircuitBreaker(open_seconds=300.0)
    breaker.record_failure("ap-northeast-2")
    assert breaker.state("ap-northeast-2") == BreakerState.OPEN


def test_open_blocks_bedrock_within_window(monkeypatch) -> None:
    breaker = CircuitBreaker(open_seconds=300.0)
    monkeypatch.setattr(breaker, "_now", _fixed_clock([0.0, 100.0, 299.9]))
    breaker.record_failure("ap-northeast-2")
    assert breaker.allow_bedrock("ap-northeast-2") is False
    assert breaker.allow_bedrock("ap-northeast-2") is False


def test_open_transitions_to_half_after_window(monkeypatch) -> None:
    breaker = CircuitBreaker(open_seconds=300.0)
    monkeypatch.setattr(breaker, "_now", _fixed_clock([0.0, 100.0, 300.0]))
    breaker.record_failure("ap-northeast-2")
    assert breaker.allow_bedrock("ap-northeast-2") is False
    assert breaker.allow_bedrock("ap-northeast-2") is True
    assert breaker.state("ap-northeast-2") == BreakerState.HALF


def test_half_success_returns_to_closed(monkeypatch) -> None:
    breaker = CircuitBreaker(open_seconds=300.0)
    monkeypatch.setattr(breaker, "_now", _fixed_clock([0.0, 300.0]))
    breaker.record_failure("ap-northeast-2")
    breaker.allow_bedrock("ap-northeast-2")
    breaker.record_success("ap-northeast-2")
    assert breaker.state("ap-northeast-2") == BreakerState.CLOSED


def test_half_failure_resets_open_window(monkeypatch) -> None:
    breaker = CircuitBreaker(open_seconds=300.0)
    monkeypatch.setattr(breaker, "_now", _fixed_clock([0.0, 300.0, 350.0, 400.0]))
    breaker.record_failure("ap-northeast-2")
    breaker.allow_bedrock("ap-northeast-2")
    breaker.record_failure("ap-northeast-2")
    assert breaker.state("ap-northeast-2") == BreakerState.OPEN
    # 50s after the second failure (at t=400), still within new 300s window.
    assert breaker.allow_bedrock("ap-northeast-2") is False


def test_regions_are_independent() -> None:
    breaker = CircuitBreaker(open_seconds=300.0)
    breaker.record_failure("ap-northeast-2")
    assert breaker.state("ap-northeast-2") == BreakerState.OPEN
    assert breaker.state("us-east-1") == BreakerState.CLOSED
    assert breaker.allow_bedrock("us-east-1") is True


def test_record_success_clears_open_state() -> None:
    breaker = CircuitBreaker(open_seconds=300.0)
    breaker.record_failure("ap-northeast-2")
    breaker.record_success("ap-northeast-2")
    assert breaker.state("ap-northeast-2") == BreakerState.CLOSED
    assert breaker.allow_bedrock("ap-northeast-2") is True
