"""Remediation Agent: proposes and (conditionally) executes actions.

Every proposed action is risk-classified (guardrails/risk.py) against the
allowlist before it ever reaches a tool call. Low-risk, allowlisted actions
in permitted namespaces auto-execute with an idempotency key; everything
else is enqueued for human approval and the graph run ends its synchronous
portion there -- execution resumes from the checkpoint once
POST /approvals/{action_id} records a decision (see api/routes/approvals.py).
"""

from __future__ import annotations

import hashlib
import json

from sre_agent.agents.deps import AgentDeps
from sre_agent.core.llm_client import ModelTier
from sre_agent.core.models import ActionType, IncidentState, RemediationAction, TimelineEvent
from sre_agent.guardrails.risk import classify_action
from sre_agent.observability.metrics import ACTION_RESULT
from sre_agent.observability.tracing import traced_node
from sre_agent.tools.schemas import (
    K8sRestartPodInput,
    K8sRollbackDeploymentInput,
    K8sScaleDeploymentInput,
)

_ACTION_SYSTEM_PROMPT = (
    "You are the remediation-planning component of an SRE incident-response "
    "agent. Given RCA hypotheses, propose 1-2 concrete remediation actions. "
    'Respond ONLY with strict JSON: {"actions": [{"action_type": '
    '"restart_pod|rollback_deployment|scale_replicas|trigger_failover|flush_cache", '
    '"target_resource": str, "parameters": {}, "rationale": str}]}. '
    "Prefer the least invasive action that plausibly resolves the incident."
)


def _idempotency_key(incident_id: str, action_type: str, target_resource: str) -> str:
    return hashlib.sha256(f"{incident_id}:{action_type}:{target_resource}".encode()).hexdigest()[
        :24
    ]


@traced_node("remediation")
def remediation_node(state: IncidentState, deps: AgentDeps) -> dict:
    incident = state["incident"]
    alert = state["raw_alert"]
    hypotheses = state.get("rca_hypotheses", [])

    prompt = json.dumps(
        {
            "service": alert.service,
            "namespace": alert.namespace,
            "hypotheses": [h.model_dump() for h in hypotheses[:3]],
        }
    )
    response = deps.llm.complete(
        incident_id=incident.incident_id,
        tier=ModelTier.FAST,
        system=_ACTION_SYSTEM_PROMPT,
        prompt=prompt,
        max_tokens=500,
    )
    try:
        parsed = json.loads(response.text)
        raw_actions = parsed.get("actions", [])
    except json.JSONDecodeError:
        raw_actions = []

    proposed: list[RemediationAction] = []
    for raw in raw_actions:
        try:
            action_type = ActionType(raw["action_type"])
        except (KeyError, ValueError):
            continue
        target_resource = raw.get("target_resource", alert.service)
        proposed.append(
            RemediationAction(
                action_type=action_type,
                target_cluster=alert.cluster,
                target_namespace=alert.namespace,
                target_resource=target_resource,
                parameters=raw.get("parameters", {}),
                idempotency_key=_idempotency_key(
                    incident.incident_id, action_type.value, target_resource
                ),
                risk_level=None,  # populated by classify_action() below
                rationale=raw.get("rationale", ""),
            )
        )

    executed: list[RemediationAction] = []
    timeline_events = []
    for action in proposed:
        decision = classify_action(action, deps.allowlist)
        action.risk_level = decision.risk_level

        if decision.blocked:
            from sre_agent.guardrails.audit import AuditEntry

            deps.audit_log.record(
                AuditEntry(
                    incident_id=incident.incident_id,
                    actor="remediation_agent",
                    event_type="action_blocked",
                    detail={"action_type": action.action_type.value, "reasons": decision.reasons},
                )
            )
            timeline_events.append(
                TimelineEvent(
                    actor="remediation_agent",
                    event_type="action_blocked",
                    detail=f"{action.action_type.value} blocked: {decision.reasons}",
                )
            )
            continue

        if not decision.requires_approval:
            result = _execute_action(action, deps)
            action.executed = True
            action.execution_result = result
            executed.append(action)
            ACTION_RESULT.labels(action_type=action.action_type.value, result="success").inc()
            timeline_events.append(
                TimelineEvent(
                    actor="remediation_agent",
                    event_type="action_executed",
                    detail=f"auto-executed {action.action_type.value} on {action.target_resource}: {result}",
                )
            )
        else:
            deps.approval_queue.enqueue(action, incident.incident_id, decision.reasons)
            timeline_events.append(
                TimelineEvent(
                    actor="remediation_agent",
                    event_type="approval_requested",
                    detail=f"{action.action_type.value} on {action.target_resource} pending approval ({decision.reasons})",
                )
            )

    return {
        "proposed_actions": proposed,
        "executed_actions": executed,
        "timeline": timeline_events,
        "next_agent": "communications",
    }


def _execute_action(action: RemediationAction, deps: AgentDeps) -> str:
    try:
        if action.action_type == ActionType.RESTART_POD:
            out = deps.k8s.restart_pod(
                K8sRestartPodInput(
                    namespace=action.target_namespace,
                    pod_name=action.target_resource,
                    idempotency_key=action.idempotency_key,
                )
            )
        elif action.action_type == ActionType.ROLLBACK_DEPLOYMENT:
            out = deps.k8s.rollback_deployment(
                K8sRollbackDeploymentInput(
                    namespace=action.target_namespace,
                    deployment=action.target_resource,
                    idempotency_key=action.idempotency_key,
                )
            )
        elif action.action_type == ActionType.SCALE_REPLICAS:
            replicas = int(action.parameters.get("replicas", 1))
            out = deps.k8s.scale_deployment(
                K8sScaleDeploymentInput(
                    namespace=action.target_namespace,
                    deployment=action.target_resource,
                    replicas=replicas,
                    idempotency_key=action.idempotency_key,
                )
            )
        else:
            return f"no-op: {action.action_type.value} has no auto-execute handler"
        return out.detail
    except Exception as exc:  # noqa: BLE001 - surfaced into execution_result for the postmortem
        ACTION_RESULT.labels(action_type=action.action_type.value, result="failure").inc()
        return f"execution failed: {exc}"
