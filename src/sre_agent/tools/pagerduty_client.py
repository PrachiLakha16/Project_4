"""PagerDuty Events/REST API client: inbound alert ingestion + ack/resolve."""

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
from sre_agent.tools.schemas import PagerDutyAckInput, PagerDutyAckOutput


class PagerDutyClientProtocol(Protocol):
    def acknowledge(self, inp: PagerDutyAckInput) -> PagerDutyAckOutput: ...


class PagerDutyClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._breaker = CircuitBreaker(
            "pagerduty",
            failure_threshold=self.settings.circuit_breaker_failure_threshold,
            reset_seconds=self.settings.circuit_breaker_reset_seconds,
        )

    @traced_tool_call("pagerduty_acknowledge")
    def acknowledge(self, inp: PagerDutyAckInput) -> PagerDutyAckOutput:
        return self._breaker.call(lambda: self._ack_impl(inp))

    @with_retries(max_attempts=3, exceptions=(httpx.HTTPError, DependencyTimeoutError))
    @with_timeout(10.0)
    def _ack_impl(self, inp: PagerDutyAckInput) -> PagerDutyAckOutput:
        resp = httpx.put(
            f"https://api.pagerduty.com/incidents/{inp.incident_id}",
            headers={
                "Authorization": f"Token token={self.settings.pagerduty_api_token}",
                "Content-Type": "application/json",
            },
            json={"incident": {"type": "incident_reference", "status": "acknowledged"}},
            timeout=self.settings.tool_call_timeout_seconds,
        )
        resp.raise_for_status()
        return PagerDutyAckOutput(ok=True, status="acknowledged")


class FakePagerDutyClient:
    def __init__(self) -> None:
        self.acked: list[str] = []

    def acknowledge(self, inp: PagerDutyAckInput) -> PagerDutyAckOutput:
        self.acked.append(inp.incident_id)
        return PagerDutyAckOutput(ok=True, status="acknowledged")
