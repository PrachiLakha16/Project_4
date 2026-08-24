"""Domain and LangGraph state schemas shared across agents, tools, and the API.

These are the pydantic contracts the whole system agrees on: alerts coming in
from Alertmanager/PagerDuty, the incident record, proposed remediation
actions, and the risk/approval envelope around them.
"""

from __future__ import annotations

import operator
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any
from uuid import uuid4

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


def _now() -> datetime:
    return datetime.now(UTC)


class Severity(str, Enum):
    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"
    SEV4 = "sev4"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ActionType(str, Enum):
    RESTART_POD = "restart_pod"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    SCALE_REPLICAS = "scale_replicas"
    TRIGGER_FAILOVER = "trigger_failover"
    FLUSH_CACHE = "flush_cache"


class AlertSource(str, Enum):
    ALERTMANAGER = "alertmanager"
    PAGERDUTY = "pagerduty"


class RawAlert(BaseModel):
    """Normalized shape of an inbound webhook payload.

    Fields sourced directly from the alert body (``annotations``, ``labels``,
    ``description``) are treated as untrusted text: they are shown to the LLM
    only through the prompt-injection sanitizer in guardrails/prompt_injection.py,
    never interpolated into tool-call arguments directly.
    """

    source: AlertSource
    fingerprint: str
    alert_name: str
    severity_hint: str | None = None
    service: str
    namespace: str = "default"
    cluster: str = "default"
    starts_at: datetime = Field(default_factory=_now)
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    generator_url: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class RemediationAction(BaseModel):
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    action_type: ActionType
    target_cluster: str
    target_namespace: str
    target_resource: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    risk_level: RiskLevel | None = None
    rationale: str
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    executed: bool = False
    execution_result: str | None = None
    created_at: datetime = Field(default_factory=_now)


class TimelineEvent(BaseModel):
    timestamp: datetime = Field(default_factory=_now)
    actor: str
    event_type: str
    detail: str


class RCAHypothesis(BaseModel):
    hypothesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(default_factory=list)
    retrieved_runbook_ids: list[str] = Field(default_factory=list)


class IncidentRecord(BaseModel):
    incident_id: str = Field(default_factory=lambda: f"INC-{uuid4().hex[:10]}")
    fingerprint: str
    severity: Severity
    service: str
    namespace: str
    cluster: str
    status: str = "open"
    is_duplicate: bool = False
    suppressed_noise: bool = False
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


def _merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    merged.update(right)
    return merged


class IncidentState(TypedDict, total=False):
    """LangGraph graph state for one incident thread.

    Short-term working memory (this dict, checkpointed to Postgres per
    incident thread_id) is intentionally separate from long-term memory
    (the pgvector RAG store of past incidents/runbooks in rag/), which is
    queried fresh on every RCA pass rather than carried in state.
    """

    incident: IncidentRecord
    raw_alert: RawAlert
    token_budget_remaining: int

    # accumulated via Annotated reducers so parallel/sequential node writes merge
    timeline: Annotated[list[TimelineEvent], operator.add]
    metric_signals: Annotated[list[dict[str, Any]], operator.add]
    log_signals: Annotated[list[dict[str, Any]], operator.add]
    trace_signals: Annotated[list[dict[str, Any]], operator.add]

    rca_hypotheses: list[RCAHypothesis]
    proposed_actions: list[RemediationAction]
    executed_actions: Annotated[list[RemediationAction], operator.add]

    comms_draft: str | None
    postmortem_draft: str | None

    next_agent: str
    error: str | None
    metadata: Annotated[dict[str, Any], _merge_dicts]
