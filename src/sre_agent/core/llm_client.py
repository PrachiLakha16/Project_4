"""Provider-agnostic LLM client with model routing and per-call cost logging.

Business logic in the agents never imports `anthropic` or `openai` directly;
it calls `LLMClient.complete(...)` with a `ModelTier`. This keeps a future
provider swap (or a per-tenant override) a one-file change.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sre_agent.core.config import Settings, get_settings
from sre_agent.observability.cost import CostLedger

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    FAST = "fast"  # triage, dedup, summarization, comms drafting
    STRONG = "strong"  # RCA reasoning over correlated telemetry + RAG


class LLMTimeoutError(RuntimeError):
    pass


class LLMBudgetExceededError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_seconds: float


class LLMClient:
    """Thin wrapper around whichever provider SDK is configured.

    Real network calls happen only via `_call_anthropic` / `_call_openai`.
    Tests inject `FakeLLMClient` (tests/fixtures) instead of this class, so no
    live credentials are required to exercise agent logic.
    """

    def __init__(
        self, settings: Settings | None = None, cost_ledger: CostLedger | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.cost_ledger = cost_ledger or CostLedger()

    def _model_for(self, tier: ModelTier) -> str:
        return (
            self.settings.llm_model_strong
            if tier == ModelTier.STRONG
            else self.settings.llm_model_fast
        )

    def complete(
        self,
        *,
        incident_id: str,
        tier: ModelTier,
        system: str,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        model = self._model_for(tier)
        remaining = self.cost_ledger.remaining_budget(
            incident_id, self.settings.incident_token_budget
        )
        if remaining <= 0:
            raise LLMBudgetExceededError(
                f"incident {incident_id} exhausted its {self.settings.incident_token_budget} token budget"
            )

        started = time.monotonic()
        try:
            if self.settings.llm_provider == "openai":
                text, in_tok, out_tok = self._call_openai(
                    model, system, prompt, max_tokens, temperature
                )
            else:
                text, in_tok, out_tok = self._call_anthropic(
                    model, system, prompt, max_tokens, temperature
                )
        except TimeoutError as exc:
            raise LLMTimeoutError(
                f"LLM call for {incident_id} timed out after {self.settings.llm_timeout_seconds}s"
            ) from exc

        latency = time.monotonic() - started
        self.cost_ledger.record(
            incident_id=incident_id, model=model, input_tokens=in_tok, output_tokens=out_tok
        )
        logger.info(
            "llm_call",
            extra={
                "incident_id": incident_id,
                "model": model,
                "tier": tier.value,
                "latency_s": round(latency, 3),
            },
        )
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_seconds=latency,
        )

    def _call_anthropic(
        self, model: str, system: str, prompt: str, max_tokens: int, temperature: float
    ) -> tuple[str, int, int]:
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.settings.anthropic_api_key, timeout=self.settings.llm_timeout_seconds
        )
        # anthropic-sdk's typed overloads for `messages.create` are strict
        # about the model/streaming literal union; call it dynamically so a
        # pinned SDK minor bump doesn't need a matching mypy chase here.
        resp = client.messages.create(  # type: ignore[call-overload]
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        return text, resp.usage.input_tokens, resp.usage.output_tokens

    def _call_openai(
        self, model: str, system: str, prompt: str, max_tokens: int, temperature: float
    ) -> tuple[str, int, int]:
        import openai

        client = openai.OpenAI(
            api_key=self.settings.openai_api_key, timeout=self.settings.llm_timeout_seconds
        )
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        return text, usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0


def get_llm_client() -> LLMClient:
    return LLMClient()


class FakeLLMClient:
    """Deterministic stand-in for `LLMClient` used by tests, the evaluation
    harness, and offline demos. `responder` maps a substring found in the
    prompt to a canned response, checked in insertion order; falls back to
    `default_response`. Still records token usage/cost through the same
    CostLedger so budget-enforcement logic is exercised identically to prod.
    """

    def __init__(
        self,
        responder: dict[str, str] | None = None,
        default_response: str = "{}",
        cost_ledger: CostLedger | None = None,
    ) -> None:
        self.responder = responder or {}
        self.default_response = default_response
        self.cost_ledger = cost_ledger or CostLedger()
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        incident_id: str,
        tier: ModelTier,
        system: str,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append({"incident_id": incident_id, "tier": tier, "prompt": prompt})
        text = self.default_response
        for key, value in self.responder.items():
            if key in prompt:
                text = value
                break
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(text) // 4)
        model = f"fake-{tier.value}"
        self.cost_ledger.record(
            incident_id=incident_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_seconds=0.001,
        )
