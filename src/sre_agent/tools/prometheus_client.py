"""Prometheus HTTP API client, wired with retries/timeout/circuit breaker.

`FakePrometheusClient` implements the same `PrometheusClientProtocol` so agent
and graph tests run without a live Prometheus.
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
from sre_agent.tools.schemas import PrometheusQueryInput, PrometheusQueryOutput


class PrometheusClientProtocol(Protocol):
    def instant_query(self, inp: PrometheusQueryInput) -> PrometheusQueryOutput: ...
    def range_query(self, inp: PrometheusQueryInput) -> PrometheusQueryOutput: ...


class PrometheusClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._breaker = CircuitBreaker(
            "prometheus",
            failure_threshold=self.settings.circuit_breaker_failure_threshold,
            reset_seconds=self.settings.circuit_breaker_reset_seconds,
        )

    @traced_tool_call("prometheus_instant_query")
    def instant_query(self, inp: PrometheusQueryInput) -> PrometheusQueryOutput:
        return self._breaker.call(lambda: self._instant_query_impl(inp))

    @with_retries(max_attempts=3, exceptions=(httpx.HTTPError, DependencyTimeoutError))
    @with_timeout(10.0)
    def _instant_query_impl(self, inp: PrometheusQueryInput) -> PrometheusQueryOutput:
        resp = httpx.get(
            f"{self.settings.prometheus_base_url}/api/v1/query",
            params={"query": inp.promql},
            timeout=self.settings.tool_call_timeout_seconds,
        )
        resp.raise_for_status()
        payload = resp.json()
        series = payload.get("data", {}).get("result", [])
        return PrometheusQueryOutput(
            query=inp.promql, series=series, summary=f"{len(series)} series returned"
        )

    @traced_tool_call("prometheus_range_query")
    def range_query(self, inp: PrometheusQueryInput) -> PrometheusQueryOutput:
        return self._breaker.call(lambda: self._range_query_impl(inp))

    @with_retries(max_attempts=3, exceptions=(httpx.HTTPError, DependencyTimeoutError))
    @with_timeout(10.0)
    def _range_query_impl(self, inp: PrometheusQueryInput) -> PrometheusQueryOutput:
        resp = httpx.get(
            f"{self.settings.prometheus_base_url}/api/v1/query_range",
            params={"query": inp.promql, "start": inp.start, "end": inp.end, "step": inp.step},
            timeout=self.settings.tool_call_timeout_seconds,
        )
        resp.raise_for_status()
        payload = resp.json()
        series = payload.get("data", {}).get("result", [])
        return PrometheusQueryOutput(
            query=inp.promql, series=series, summary=f"{len(series)} series returned"
        )


class FakePrometheusClient:
    """Deterministic fixture client used by tests and local/offline demos."""

    def __init__(self, canned_series: list[dict] | None = None) -> None:
        self.canned_series = canned_series or [
            {"metric": {"pod": "checkout-7d9f-abc12"}, "value": [1724, "0.93"]},
        ]

    def instant_query(self, inp: PrometheusQueryInput) -> PrometheusQueryOutput:
        return PrometheusQueryOutput(
            query=inp.promql,
            series=self.canned_series,
            summary=f"{len(self.canned_series)} series (fake)",
        )

    def range_query(self, inp: PrometheusQueryInput) -> PrometheusQueryOutput:
        return PrometheusQueryOutput(
            query=inp.promql,
            series=self.canned_series,
            summary=f"{len(self.canned_series)} series (fake)",
        )
