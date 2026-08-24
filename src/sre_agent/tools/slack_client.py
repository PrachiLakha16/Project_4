"""Slack ChatOps client: incident channel updates and human-approval prompts.

The approval flow posts an interactive-style message (block layout described
in-line for a real Slack app to render buttons); the actual approve/reject
decision is submitted back through the FastAPI `/approvals` endpoint
(api/routes/approvals.py), not parsed from Slack's interactivity payload here
-- keeps the trust boundary at one authenticated API surface.
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
from sre_agent.tools.schemas import SlackPostInput, SlackPostOutput


class SlackClientProtocol(Protocol):
    def post_message(self, inp: SlackPostInput) -> SlackPostOutput: ...


class SlackClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._breaker = CircuitBreaker(
            "slack",
            failure_threshold=self.settings.circuit_breaker_failure_threshold,
            reset_seconds=self.settings.circuit_breaker_reset_seconds,
        )

    @traced_tool_call("slack_post_message")
    def post_message(self, inp: SlackPostInput) -> SlackPostOutput:
        return self._breaker.call(lambda: self._post_impl(inp))

    @with_retries(max_attempts=3, exceptions=(httpx.HTTPError, DependencyTimeoutError))
    @with_timeout(10.0)
    def _post_impl(self, inp: SlackPostInput) -> SlackPostOutput:
        resp = httpx.post(
            self.settings.slack_webhook_url,
            json={"channel": inp.channel, "text": inp.text, "thread_ts": inp.thread_ts},
            timeout=self.settings.tool_call_timeout_seconds,
        )
        resp.raise_for_status()
        return SlackPostOutput(ok=True, ts=resp.headers.get("x-slack-ts", "0"))


class FakeSlackClient:
    def __init__(self) -> None:
        self.sent: list[SlackPostInput] = []

    def post_message(self, inp: SlackPostInput) -> SlackPostOutput:
        self.sent.append(inp)
        return SlackPostOutput(ok=True, ts=f"fake-{len(self.sent)}")
