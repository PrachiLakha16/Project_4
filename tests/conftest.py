"""Shared pytest fixtures for the sre_agent test suite.

Every fixture here builds on the fake/in-memory implementations that already
ship in the source tree (`build_fake_deps`, `FakeLLMClient`, `Fake*Client`)
so the whole test suite runs offline: no live LLM, Postgres, or Kubernetes
cluster is ever required.
"""

from __future__ import annotations

import pytest

from sre_agent.agents.deps import AgentDeps, build_fake_deps
from sre_agent.core.llm_client import FakeLLMClient
from tests.factories import make_alert, make_incident

__all__ = [
    "fake_deps",
    "alert_factory",
    "incident_factory",
    "scripted_llm",
    "make_alert",
    "make_incident",
]


@pytest.fixture
def fake_deps() -> AgentDeps:
    """AgentDeps wired entirely with fakes and a default FakeLLMClient."""
    return build_fake_deps()


@pytest.fixture
def alert_factory():
    return make_alert


@pytest.fixture
def incident_factory():
    return make_incident


@pytest.fixture
def scripted_llm():
    """Factory for a FakeLLMClient with caller-supplied responder/default."""

    def _build(responder: dict | None = None, default_response: str = "{}") -> FakeLLMClient:
        return FakeLLMClient(responder=responder or {}, default_response=default_response)

    return _build
