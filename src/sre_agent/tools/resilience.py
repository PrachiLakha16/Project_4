"""Failure-handling primitives shared by every external tool client.

- `with_retries`: exponential backoff + full jitter, via tenacity.
- `with_timeout`: hard wall-clock timeout on any blocking call.
- `CircuitBreaker`: trips open after N consecutive failures on a dependency
  and short-circuits further calls until a cooldown elapses, so a flaky
  Prometheus/Loki/K8s API doesn't stall the whole graph run.
- `idempotency`: a small in-memory (swap for Redis/Postgres in prod) store so
  a retried remediation action does not double-execute.
"""

from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, TypeVar

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from sre_agent.observability.metrics import CIRCUIT_BREAKER_STATE

F = TypeVar("F", bound=Callable[..., Any])


class DependencyTimeoutError(TimeoutError):
    pass


class CircuitBreakerOpenError(RuntimeError):
    pass


def with_retries(
    *, max_attempts: int = 3, exceptions: tuple[type[Exception], ...] = (Exception,)
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(initial=0.2, max=5.0),
            retry=retry_if_exception_type(exceptions),
            reraise=True,
        )
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def with_timeout(seconds: float) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(func, *args, **kwargs)
                try:
                    return future.result(timeout=seconds)
                except FutureTimeoutError as exc:
                    raise DependencyTimeoutError(
                        f"{func.__name__} exceeded {seconds}s timeout"
                    ) from exc

        return wrapper  # type: ignore[return-value]

    return decorator


class CircuitBreaker:
    """Per-dependency breaker: closed -> open after N failures -> half-open after cooldown."""

    def __init__(
        self, name: str, *, failure_threshold: int = 5, reset_seconds: float = 30.0
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            # half-open once the cooldown elapses: allow a trial call through
            return time.monotonic() - self._opened_at < self.reset_seconds

    def call(self, func: Callable[[], Any]) -> Any:
        if self.is_open:
            CIRCUIT_BREAKER_STATE.labels(dependency=self.name).set(1)
            raise CircuitBreakerOpenError(f"circuit breaker for '{self.name}' is open")
        try:
            result = func()
        except Exception:
            self._record_failure()
            raise
        else:
            self._record_success()
            return result

    def _record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()
        CIRCUIT_BREAKER_STATE.labels(dependency=self.name).set(1 if self._opened_at else 0)

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
        CIRCUIT_BREAKER_STATE.labels(dependency=self.name).set(0)


class IdempotencyStore:
    """Guards remediation actions against double-execution on retry.

    Keyed by the action's idempotency_key (deterministic: action_type +
    target_resource + a per-incident nonce). Production deployments back this
    with the same Postgres instance as the checkpointer (a small
    `executed_actions` table with a unique constraint), so the guarantee
    survives process restarts; this in-memory version is what tests exercise.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, Any] = {}

    def already_executed(self, idempotency_key: str) -> bool:
        with self._lock:
            return idempotency_key in self._seen

    def mark_executed(self, idempotency_key: str, result: Any) -> None:
        with self._lock:
            self._seen[idempotency_key] = result

    def get_result(self, idempotency_key: str) -> Any:
        with self._lock:
            return self._seen.get(idempotency_key)
