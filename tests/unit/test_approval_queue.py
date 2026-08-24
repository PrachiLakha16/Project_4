"""Tests for guardrails.approval_queue.ApprovalQueue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sre_agent.core.models import ActionType, ApprovalStatus, RemediationAction
from sre_agent.guardrails.approval_queue import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    ApprovalQueue,
)
from sre_agent.guardrails.audit import InMemoryAuditLog


def _action() -> RemediationAction:
    return RemediationAction(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        target_cluster="prod-us-east-1",
        target_namespace="prod",
        target_resource="checkout",
        idempotency_key="idem-1",
        rationale="bad deploy",
    )


def test_enqueue_sets_pending_status_and_appears_in_list_pending():
    audit_log = InMemoryAuditLog()
    queue = ApprovalQueue(audit_log=audit_log)
    action = _action()

    pending = queue.enqueue(action, "INC-1", ["rollback always requires approval"])

    assert action.approval_status == ApprovalStatus.PENDING
    assert pending.incident_id == "INC-1"
    listed = queue.list_pending()
    assert len(listed) == 1
    assert listed[0].action.action_id == action.action_id


def test_list_pending_filters_by_incident_id():
    queue = ApprovalQueue(audit_log=InMemoryAuditLog())
    a1, a2 = _action(), _action()
    queue.enqueue(a1, "INC-1", [])
    queue.enqueue(a2, "INC-2", [])

    assert [p.action.action_id for p in queue.list_pending("INC-1")] == [a1.action_id]
    assert len(queue.list_pending()) == 2


def test_decide_approve_removes_from_queue_and_sets_status():
    queue = ApprovalQueue(audit_log=InMemoryAuditLog())
    action = _action()
    queue.enqueue(action, "INC-1", [])

    decided = queue.decide(action.action_id, approved=True, decided_by="alice")

    assert decided.action.approval_status == ApprovalStatus.APPROVED
    assert decided.decided_by == "alice"
    assert queue.list_pending() == []


def test_decide_reject_sets_rejected_status():
    queue = ApprovalQueue(audit_log=InMemoryAuditLog())
    action = _action()
    queue.enqueue(action, "INC-1", [])

    decided = queue.decide(action.action_id, approved=False, decided_by="bob")

    assert decided.action.approval_status == ApprovalStatus.REJECTED


def test_decide_unknown_id_raises_not_found():
    queue = ApprovalQueue(audit_log=InMemoryAuditLog())
    with pytest.raises(ApprovalNotFoundError):
        queue.decide("does-not-exist", approved=True, decided_by="alice")


def test_decide_after_removal_raises_not_found():
    """Once decide() succeeds the entry is removed from the queue entirely,
    so a second call for the same action_id surfaces as "not found" rather
    than "already decided" -- the queue is the source of truth for what's
    still pending."""
    queue = ApprovalQueue(audit_log=InMemoryAuditLog())
    action = _action()
    queue.enqueue(action, "INC-1", [])
    queue.decide(action.action_id, approved=True, decided_by="alice")

    with pytest.raises(ApprovalNotFoundError):
        queue.decide(action.action_id, approved=True, decided_by="alice")


def test_decide_on_stale_non_pending_entry_still_in_queue_raises_already_decided():
    """Reaches the ApprovalAlreadyDecidedError branch directly: an entry whose
    action status was mutated to non-PENDING out from under the queue (e.g. a
    concurrent writer) but that has not yet been removed."""
    queue = ApprovalQueue(audit_log=InMemoryAuditLog())
    action = _action()
    queue.enqueue(action, "INC-1", [])
    # Simulate a decision recorded elsewhere without going through decide().
    action.approval_status = ApprovalStatus.APPROVED

    with pytest.raises(ApprovalAlreadyDecidedError):
        queue.decide(action.action_id, approved=True, decided_by="alice")


def test_expire_stale_marks_expired_and_removes_from_pending():
    queue = ApprovalQueue(audit_log=InMemoryAuditLog(), stale_after_minutes=15)
    action = _action()
    pending = queue.enqueue(action, "INC-1", [])
    # Backdate the request so it's older than the staleness window.
    pending.requested_at = datetime.now(UTC) - timedelta(minutes=20)

    expired = queue.expire_stale()

    assert len(expired) == 1
    assert action.approval_status == ApprovalStatus.EXPIRED
    assert queue.list_pending() == []


def test_expire_stale_leaves_fresh_entries_pending():
    queue = ApprovalQueue(audit_log=InMemoryAuditLog(), stale_after_minutes=15)
    action = _action()
    queue.enqueue(action, "INC-1", [])

    expired = queue.expire_stale()

    assert expired == []
    assert len(queue.list_pending()) == 1


def test_get_raises_not_found_for_unknown_id():
    queue = ApprovalQueue(audit_log=InMemoryAuditLog())
    with pytest.raises(ApprovalNotFoundError):
        queue.get("nope")


def test_audit_log_receives_enqueue_and_decide_entries():
    audit_log = InMemoryAuditLog()
    queue = ApprovalQueue(audit_log=audit_log)
    action = _action()
    queue.enqueue(action, "INC-1", ["needs review"])
    queue.decide(action.action_id, approved=True, decided_by="alice")

    entries = audit_log.for_incident("INC-1")
    event_types = [e.event_type for e in entries]
    assert "approval_requested" in event_types
    assert "approval_decided" in event_types


def test_audit_log_receives_expire_entry():
    audit_log = InMemoryAuditLog()
    queue = ApprovalQueue(audit_log=audit_log, stale_after_minutes=15)
    action = _action()
    pending = queue.enqueue(action, "INC-1", [])
    pending.requested_at = datetime.now(UTC) - timedelta(minutes=30)

    queue.expire_stale()

    event_types = [e.event_type for e in audit_log.for_incident("INC-1")]
    assert "approval_expired" in event_types
