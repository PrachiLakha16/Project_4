"""Dependency bundle threaded through every agent node.

LangGraph nodes are plain functions of `(state) -> partial_state`; to keep
them testable and to avoid constructing clients (real or fake) inside node
bodies, every node is built via a small factory that closes over an
`AgentDeps` instance. Tests construct `AgentDeps` with the Fake* clients;
`build_default_deps()` wires the real ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sre_agent.core.config import Settings, get_settings
from sre_agent.core.llm_client import FakeLLMClient, LLMClient
from sre_agent.guardrails.allowlist import AllowlistPolicy, load_allowlist
from sre_agent.guardrails.approval_queue import ApprovalQueue
from sre_agent.guardrails.audit import AuditLogProtocol, InMemoryAuditLog
from sre_agent.rag.retriever import RunbookRetriever
from sre_agent.tools.k8s_client import K8sClientProtocol
from sre_agent.tools.loki_client import LogClientProtocol
from sre_agent.tools.pagerduty_client import PagerDutyClientProtocol
from sre_agent.tools.prometheus_client import PrometheusClientProtocol
from sre_agent.tools.resilience import IdempotencyStore
from sre_agent.tools.slack_client import SlackClientProtocol


@dataclass
class AgentDeps:
    settings: Settings
    llm: LLMClient | FakeLLMClient
    prometheus: PrometheusClientProtocol
    logs: LogClientProtocol
    k8s: K8sClientProtocol
    slack: SlackClientProtocol
    pagerduty: PagerDutyClientProtocol
    retriever: RunbookRetriever
    allowlist: AllowlistPolicy
    approval_queue: ApprovalQueue
    audit_log: AuditLogProtocol
    idempotency: IdempotencyStore = field(default_factory=IdempotencyStore)
    seen_fingerprints: dict[str, str] = field(
        default_factory=dict
    )  # fingerprint -> incident_id, for dedup


def build_default_deps() -> AgentDeps:
    """Wires real clients for the running FastAPI service."""
    from sre_agent.tools.k8s_client import K8sClient
    from sre_agent.tools.loki_client import LokiClient
    from sre_agent.tools.pagerduty_client import PagerDutyClient
    from sre_agent.tools.prometheus_client import PrometheusClient
    from sre_agent.tools.slack_client import SlackClient

    settings = get_settings()
    idempotency = IdempotencyStore()
    audit_log = InMemoryAuditLog()
    return AgentDeps(
        settings=settings,
        llm=LLMClient(settings=settings),
        prometheus=PrometheusClient(settings=settings),
        logs=LokiClient(settings=settings),
        k8s=K8sClient(settings=settings, idempotency_store=idempotency),
        slack=SlackClient(settings=settings),
        pagerduty=PagerDutyClient(settings=settings),
        retriever=RunbookRetriever(),
        allowlist=load_allowlist(settings.allowlist_config_path),
        approval_queue=ApprovalQueue(
            audit_log=audit_log, stale_after_minutes=settings.approval_queue_stale_minutes
        ),
        audit_log=audit_log,
        idempotency=idempotency,
    )


def build_fake_deps(llm: LLMClient | FakeLLMClient | None = None) -> AgentDeps:
    """Wires fake/in-memory clients: used by tests, the eval harness, and
    local demos that shouldn't touch real infra."""
    from sre_agent.tools.k8s_client import FakeK8sClient
    from sre_agent.tools.loki_client import FakeLokiClient
    from sre_agent.tools.pagerduty_client import FakePagerDutyClient
    from sre_agent.tools.prometheus_client import FakePrometheusClient
    from sre_agent.tools.slack_client import FakeSlackClient

    settings = get_settings()
    audit_log = InMemoryAuditLog()
    fake_k8s = FakeK8sClient()
    return AgentDeps(
        settings=settings,
        llm=llm or FakeLLMClient(),
        prometheus=FakePrometheusClient(),
        logs=FakeLokiClient(),
        k8s=fake_k8s,
        slack=FakeSlackClient(),
        pagerduty=FakePagerDutyClient(),
        retriever=RunbookRetriever(),
        allowlist=load_allowlist(settings.allowlist_config_path),
        approval_queue=ApprovalQueue(
            audit_log=audit_log, stale_after_minutes=settings.approval_queue_stale_minutes
        ),
        audit_log=audit_log,
        idempotency=fake_k8s.idempotency_store,
    )
