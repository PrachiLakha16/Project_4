"""Tests for tools.resilience: retries, timeout, circuit breaker, idempotency."""

from __future__ import annotations

import time

import pytest

from sre_agent.tools.resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    DependencyTimeoutError,
    IdempotencyStore,
    with_retries,
    with_timeout,
)


def test_with_retries_retries_then_raises():
    attempts = {"count": 0}

    @with_retries(max_attempts=3, exceptions=(ValueError,))
    def flaky():
        attempts["count"] += 1
        raise ValueError("always fails")

    with pytest.raises(ValueError):
        flaky()

    assert attempts["count"] == 3


def test_with_retries_succeeds_after_transient_failures():
    attempts = {"count": 0}

    @with_retries(max_attempts=3, exceptions=(ValueError,))
    def eventually_succeeds():
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise ValueError("transient")
        return "ok"

    assert eventually_succeeds() == "ok"
    assert attempts["count"] == 2


def test_with_retries_does_not_retry_unrelated_exceptions():
    attempts = {"count": 0}

    @with_retries(max_attempts=3, exceptions=(ValueError,))
    def raises_type_error():
        attempts["count"] += 1
        raise TypeError("not retried")

    with pytest.raises(TypeError):
        raises_type_error()

    assert attempts["count"] == 1


def test_with_timeout_raises_on_slow_function():
    @with_timeout(0.1)
    def slow():
        time.sleep(1.0)
        return "too late"

    with pytest.raises(DependencyTimeoutError):
        slow()


def test_with_timeout_returns_result_when_fast_enough():
    @with_timeout(2.0)
    def fast():
        return "done"

    assert fast() == "done"


def test_circuit_breaker_opens_after_failure_threshold():
    breaker = CircuitBreaker("test-dep", failure_threshold=3, reset_seconds=30.0)

    def failing():
        raise RuntimeError("boom")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            breaker.call(failing)

    assert breaker.is_open is True
    with pytest.raises(CircuitBreakerOpenError):
        breaker.call(failing)


def test_circuit_breaker_allows_trial_call_after_reset_seconds():
    breaker = CircuitBreaker("test-dep", failure_threshold=2, reset_seconds=0.05)

    def failing():
        raise RuntimeError("boom")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(failing)
    assert breaker.is_open is True

    time.sleep(0.1)
    assert breaker.is_open is False  # half-open: trial call allowed

    result = breaker.call(lambda: "recovered")
    assert result == "recovered"
    assert breaker.is_open is False


def test_circuit_breaker_success_resets_failure_count():
    breaker = CircuitBreaker("test-dep", failure_threshold=3, reset_seconds=30.0)

    def failing():
        raise RuntimeError("boom")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(failing)

    breaker.call(lambda: "ok")  # resets failure count
    assert breaker.is_open is False

    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(failing)
    # Still only 2 consecutive failures since the reset -- should not be open yet.
    assert breaker.is_open is False


def test_idempotency_store_round_trip():
    store = IdempotencyStore()
    assert store.already_executed("key-1") is False
    assert store.get_result("key-1") is None

    store.mark_executed("key-1", {"status": "done"})

    assert store.already_executed("key-1") is True
    assert store.get_result("key-1") == {"status": "done"}


def test_idempotency_store_distinguishes_keys():
    store = IdempotencyStore()
    store.mark_executed("key-a", "result-a")
    assert store.already_executed("key-b") is False
    assert store.get_result("key-b") is None
