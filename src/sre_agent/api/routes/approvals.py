"""Human-approval-gate API: list pending remediation actions, approve/reject.

`POST /approvals/{action_id}` requires the `sre-oncall` role (see
api/rbac.py) -- this is the single authorization boundary for turning a
proposed high-risk action into an executed one; the Slack ChatOps message is
a notification only, never itself authorization.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from sre_agent.api.rbac import Principal, require_oncall
from sre_agent.guardrails.approval_queue import ApprovalAlreadyDecidedError, ApprovalNotFoundError

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    note: str | None = None


class PendingApprovalResponse(BaseModel):
    action_id: str
    incident_id: str
    action_type: str
    target_resource: str
    risk_level: str | None
    reasons: list[str]
    requested_at: str


@router.get("", response_model=list[PendingApprovalResponse])
def list_pending(incident_id: str | None = None) -> list[PendingApprovalResponse]:
    from sre_agent.api.app import get_app_state

    queue = get_app_state().deps.approval_queue
    return [
        PendingApprovalResponse(
            action_id=p.action.action_id,
            incident_id=p.incident_id,
            action_type=p.action.action_type.value,
            target_resource=p.action.target_resource,
            risk_level=p.action.risk_level.value if p.action.risk_level else None,
            reasons=p.reasons,
            requested_at=p.requested_at.isoformat(),
        )
        for p in queue.list_pending(incident_id)
    ]


@router.post("/{action_id}")
def decide_approval(
    action_id: str,
    decision: ApprovalDecisionRequest,
    principal: Principal = Depends(require_oncall),
) -> dict:
    from sre_agent.api.app import get_app_state

    app_state = get_app_state()
    queue = app_state.deps.approval_queue
    try:
        pending = queue.decide(action_id, approved=decision.approved, decided_by=principal.subject)
    except ApprovalNotFoundError:
        return {"error": "not_found", "action_id": action_id}
    except ApprovalAlreadyDecidedError as exc:
        return {"error": "already_decided", "detail": str(exc)}

    result = {
        "action_id": action_id,
        "status": pending.action.approval_status.value,
        "decided_by": principal.subject,
    }
    if decision.approved:
        from sre_agent.agents.remediation import _execute_action

        execution_result = _execute_action(pending.action, app_state.deps)
        pending.action.executed = True
        pending.action.execution_result = execution_result
        result["execution_result"] = execution_result
    return result
