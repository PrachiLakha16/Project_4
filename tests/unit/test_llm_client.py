"""Tests for core.llm_client: FakeLLMClient behavior, CostLedger math, and
LLMClient's pre-network budget enforcement.
"""

from __future__ import annotations

import pytest

from sre_agent.core.config import Settings
from sre_agent.core.llm_client import FakeLLMClient, LLMBudgetExceededError, LLMClient, ModelTier
from sre_agent.observability.cost import CostLedger


def test_fake_llm_client_returns_responder_match():
    llm = FakeLLMClient(
        responder={"severity_hint": "SEVERITY_RESPONSE"}, default_response="DEFAULT"
    )
    response = llm.complete(
        incident_id="INC-1",
        tier=ModelTier.FAST,
        system="sys",
        prompt='{"severity_hint": "warning"}',
    )
    assert response.text == "SEVERITY_RESPONSE"


def test_fake_llm_client_falls_back_to_default_response():
    llm = FakeLLMClient(responder={"no_match_here": "X"}, default_response="DEFAULT")
    response = llm.complete(
        incident_id="INC-1", tier=ModelTier.FAST, system="sys", prompt="unrelated prompt"
    )
    assert response.text == "DEFAULT"


def test_fake_llm_client_checks_responder_in_insertion_order():
    llm = FakeLLMClient(responder={"a": "first", "ab": "second"}, default_response="DEFAULT")
    response = llm.complete(incident_id="INC-1", tier=ModelTier.FAST, system="sys", prompt="ab")
    assert response.text == "first"  # "a" is checked first and matches as a substring of "ab"


def test_fake_llm_client_records_calls():
    llm = FakeLLMClient(default_response="{}")
    llm.complete(incident_id="INC-1", tier=ModelTier.STRONG, system="sys", prompt="hello")
    assert len(llm.calls) == 1
    assert llm.calls[0]["incident_id"] == "INC-1"
    assert llm.calls[0]["tier"] == ModelTier.STRONG
    assert llm.calls[0]["prompt"] == "hello"


def test_fake_llm_client_records_cost_via_shared_ledger():
    ledger = CostLedger()
    llm = FakeLLMClient(default_response="response text", cost_ledger=ledger)
    llm.complete(incident_id="INC-1", tier=ModelTier.FAST, system="sys", prompt="a prompt")
    summary = ledger.summary("INC-1")
    assert summary["calls"] == 1
    assert summary["total_tokens"] > 0


def test_cost_ledger_remaining_budget_decreases_with_usage():
    ledger = CostLedger()
    assert ledger.remaining_budget("INC-1", 1000) == 1000

    ledger.record(incident_id="INC-1", model="claude-haiku-4-5", input_tokens=100, output_tokens=50)
    assert ledger.remaining_budget("INC-1", 1000) == 850


def test_cost_ledger_remaining_budget_is_per_incident():
    ledger = CostLedger()
    ledger.record(incident_id="INC-1", model="claude-haiku-4-5", input_tokens=500, output_tokens=0)
    assert ledger.remaining_budget("INC-1", 1000) == 500
    assert ledger.remaining_budget("INC-2", 1000) == 1000


def test_cost_ledger_summary_computes_usd_and_token_totals():
    ledger = CostLedger()
    ledger.record(
        incident_id="INC-1", model="claude-sonnet-5", input_tokens=1000, output_tokens=1000
    )
    summary = ledger.summary("INC-1")
    assert summary["total_tokens"] == 2000
    # 1000/1000 * 0.003 + 1000/1000 * 0.015 = 0.018
    assert summary["total_usd"] == pytest.approx(0.018, rel=1e-6)
    assert summary["calls"] == 1


def test_cost_ledger_summary_unknown_incident_is_zeroed():
    ledger = CostLedger()
    summary = ledger.summary("no-such-incident")
    assert summary == {"total_tokens": 0, "total_usd": 0.0, "calls": 0}


def test_cost_ledger_unpriced_model_uses_default_price():
    ledger = CostLedger()
    entry = ledger.record(
        incident_id="INC-1", model="some-unknown-model", input_tokens=1000, output_tokens=1000
    )
    # default price: 0.002 input + 0.010 output per 1K
    assert entry.usd == pytest.approx(0.012, rel=1e-6)


def test_llm_client_raises_budget_exceeded_before_network_call():
    """The budget check happens before any provider call, so this must raise
    without needing network access or a real API key."""
    ledger = CostLedger()
    settings = Settings(incident_token_budget=10, anthropic_api_key="", llm_provider="anthropic")
    client = LLMClient(settings=settings, cost_ledger=ledger)

    # Pre-exhaust the budget for this incident.
    ledger.record(incident_id="INC-1", model="claude-haiku-4-5", input_tokens=20, output_tokens=0)

    with pytest.raises(LLMBudgetExceededError):
        client.complete(incident_id="INC-1", tier=ModelTier.FAST, system="sys", prompt="hello")


def test_llm_client_model_for_tier_routes_fast_and_strong():
    settings = Settings(llm_model_fast="fast-model", llm_model_strong="strong-model")
    client = LLMClient(settings=settings)
    assert client._model_for(ModelTier.FAST) == "fast-model"
    assert client._model_for(ModelTier.STRONG) == "strong-model"
