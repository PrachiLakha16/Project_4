"""Pending-approval queue for actions that failed the auto-execute gate.

Exposed to humans via the ChatOps Slack message (posted by the Communications
Agent) and via the FastAPI `/approvals` endpoints (api/routes/approvals.py),
which is what actually records the decision -- Slack is a notification
surface, not the authorization boundary (see prompt_injection.py's docstring
on trust boundaries, and SECURITY.md).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sre_agent.core.models import ApprovalStatus, RemediationAction
from sre_agent.guardrails.audit import AuditEntry, AuditLogProtocol, InMemoryAuditLog
from sre_agent.observability.metrics import APPROVAL_QUEUE_DEPTH


class ApprovalNotFoundError(KeyError):
    pass


class ApprovalAlreadyDecidedError(RuntimeError):
    pass


@dataclass
class PendingApproval:
    action: RemediationAction
    incident_id: str
    reasons: list[str]
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None
    decided_by: str | None = None


class ApprovalQueue:
    """In-memory queue keyed by action_id. Production backs this with a
    Postgres table (`pending_approvals`) so the queue survives a pod restart
    mid-incident; the interface here is what the FastAPI layer and tests use,
    so swapping the backing store doesn't ripple outward."""

    def __init__(
        self, audit_log: AuditLogProtocol | None = None, stale_after_minutes: int = 15
    ) -> None:
        self._lock = threading.Lock()
        self._queue: dict[str, PendingApproval] = {}
        self.audit_log = audit_log or InMemoryAuditLog()
        self.stale_after_minutes = stale_after_minutes

    def enqueue(
        self, action: RemediationAction, incident_id: str, reasons: list[str]
    ) -> PendingApproval:
        action.approval_status = ApprovalStatus.PENDING
        pending = PendingApproval(action=action, incident_id=incident_id, reasons=reasons)
        with self._lock:
            self._queue[action.action_id] = pending
            APPROVAL_QUEUE_DEPTH.set(len(self._queue))
        self.audit_log.record(
            AuditEntry(
                incident_id=incident_id,
                actor="guardrails.approval_queue",
                event_type="approval_requested",
                detail={"action_id": action.action_id, "reasons": reasons},
            )
        )
        return pending

    def decide(self, action_id: str, *, approved: bool, decided_by: str) -> PendingApproval:
        with self._lock:
            pending = self._queue.get(action_id)
            if pending is None:
                raise ApprovalNotFoundError(action_id)
            if pending.action.approval_status != ApprovalStatus.PENDING:
                raise ApprovalAlreadyDecidedError(
                    f"{action_id} already {pending.action.approval_status.value}"
                )
            pending.action.approval_status = (
                ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            )
            pending.decided_at = datetime.now(UTC)
            pending.decided_by = decided_by
            del self._queue[action_id]
            APPROVAL_QUEUE_DEPTH.set(len(self._queue))
        self.audit_log.record(
            AuditEntry(
                incident_id=pending.incident_id,
                actor=decided_by,
                event_type="approval_decided",
                detail={"action_id": action_id, "approved": approved},
            )
        )
        return pending

    def get(self, action_id: str) -> PendingApproval:
        with self._lock:
            pending = self._queue.get(action_id)
        if pending is None:
            raise ApprovalNotFoundError(action_id)
        return pending

    def list_pending(self, incident_id: str | None = None) -> list[PendingApproval]:
        with self._lock:
            values = list(self._queue.values())
        if incident_id:
            values = [p for p in values if p.incident_id == incident_id]
        return sorted(values, key=lambda p: p.requested_at)

    def expire_stale(self) -> list[PendingApproval]:
        """Marks anything older than stale_after_minutes as EXPIRED so it
        stops blocking incident resolution; called by a periodic sweep. This
        realizes the agent-service SLO of bounded approval-queue staleness
        (see docs/runbooks/agent-service-slos.md)."""
        cutoff = datetime.now(UTC) - timedelta(minutes=self.stale_after_minutes)
        expired: list[PendingApproval] = []
        with self._lock:
            for action_id, pending in list(self._queue.items()):
                if pending.requested_at < cutoff:
                    pending.action.approval_status = ApprovalStatus.EXPIRED
                    expired.append(pending)
                    del self._queue[action_id]
            APPROVAL_QUEUE_DEPTH.set(len(self._queue))
        for pending in expired:
            self.audit_log.record(
                AuditEntry(
                    incident_id=pending.incident_id,
                    actor="guardrails.approval_queue",
                    event_type="approval_expired",
                    detail={"action_id": pending.action.action_id},
                )
            )
        return expired
