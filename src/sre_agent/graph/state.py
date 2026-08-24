"""Explicit LangGraph state schema for one incident thread.

Re-exported from core.models so both the graph wiring and the rest of the
codebase (API layer, evaluation harness) share a single definition. Kept in
core.models because RemediationAction/IncidentRecord/etc are also used
outside the graph (API responses, audit log), and importing "state" from
"graph" into those call sites would invert the dependency direction.

Short-term memory: everything in IncidentState is checkpointed per
incident_id (the LangGraph thread_id) via graph/checkpointer.py and lives
only as long as the incident is open/being reviewed.

Long-term memory: the RAG store in rag/vector_store.py, queried fresh by the
Diagnostic Agent on every RCA pass -- it is never copied into IncidentState,
so it stays authoritative and up to date across incidents.
"""

from __future__ import annotations

from sre_agent.core.models import (
    ActionType,
    ApprovalStatus,
    IncidentRecord,
    IncidentState,
    RawAlert,
    RCAHypothesis,
    RemediationAction,
    RiskLevel,
    Severity,
    TimelineEvent,
)

__all__ = [
    "ActionType",
    "ApprovalStatus",
    "IncidentRecord",
    "IncidentState",
    "RawAlert",
    "RCAHypothesis",
    "RemediationAction",
    "RiskLevel",
    "Severity",
    "TimelineEvent",
    "new_incident_state",
]


def new_incident_state(
    alert: RawAlert, incident: IncidentRecord, token_budget: int
) -> IncidentState:
    return IncidentState(
        incident=incident,
        raw_alert=alert,
        token_budget_remaining=token_budget,
        timeline=[
            TimelineEvent(
                actor="system",
                event_type="incident_created",
                detail=f"created from alert {alert.fingerprint}",
            )
        ],
        metric_signals=[],
        log_signals=[],
        trace_signals=[],
        rca_hypotheses=[],
        proposed_actions=[],
        executed_actions=[],
        comms_draft=None,
        postmortem_draft=None,
        next_agent="triage",
        error=None,
        metadata={},
    )
