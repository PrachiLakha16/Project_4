"""Loki (or CloudWatch-Logs-Insights-style) log query client.

Same real/fake split as prometheus_client.py: `LokiClient` hits a real Loki
HTTP API, `FakeLokiClient` returns canned log lines for tests/local demos.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from sre_agent.core.config import Settings, get_settings
from sre_agent.observability.tracing import traced_tool_call
from sre_agent.tools.resilience import (
    CircuitBreaker,
    DependencyTimeoutError,
    with_retries,
    with_timeout,
)
from sre_agent.tools.schemas import LogQueryInput, LogQueryOutput


class LogClientProtocol(Protocol):
    def query_logs(self, inp: LogQueryInput) -> LogQueryOutput: ...


class LokiClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._breaker = CircuitBreaker(
            "loki",
            failure_threshold=self.settings.circuit_breaker_failure_threshold,
            reset_seconds=self.settings.circuit_breaker_reset_seconds,
        )

    @traced_tool_call("loki_query_logs")
    def query_logs(self, inp: LogQueryInput) -> LogQueryOutput:
        return self._breaker.call(lambda: self._query_impl(inp))

    @with_retries(max_attempts=3, exceptions=(httpx.HTTPError, DependencyTimeoutError))
    @with_timeout(10.0)
    def _query_impl(self, inp: LogQueryInput) -> LogQueryOutput:
        logql = f'{{namespace="{inp.namespace}",service="{inp.service}"}}'
        if inp.filter_text:
            logql += f' |= "{inp.filter_text}"'
        resp = httpx.get(
            f"{self.settings.loki_base_url}/loki/api/v1/query_range",
            params={"query": logql, "limit": 200},
            timeout=self.settings.tool_call_timeout_seconds,
        )
        resp.raise_for_status()
        payload = resp.json()
        lines: list[str] = []
        for stream in payload.get("data", {}).get("result", []):
            lines.extend(value[1] for value in stream.get("values", []))
        error_count = sum(
            1 for line in lines if "error" in line.lower() or "exception" in line.lower()
        )
        return LogQueryOutput(service=inp.service, lines=lines, error_count=error_count)


class FakeLokiClient:
    def __init__(self, canned_lines: list[str] | None = None) -> None:
        self.canned_lines = canned_lines or [
            "2026-08-24T10:02:11Z ERROR checkout-svc: connection pool exhausted talking to payments-db",
            "2026-08-24T10:02:12Z ERROR checkout-svc: connection pool exhausted talking to payments-db",
            "2026-08-24T10:02:14Z WARN checkout-svc: retrying payments-db connection (attempt 3)",
        ]

    def query_logs(self, inp: LogQueryInput) -> LogQueryOutput:
        error_count = sum(1 for line in self.canned_lines if "error" in line.lower())
        return LogQueryOutput(service=inp.service, lines=self.canned_lines, error_count=error_count)
