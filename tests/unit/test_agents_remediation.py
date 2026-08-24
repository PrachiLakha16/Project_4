"""Tests for agents.remediation.remediation_node."""

from __future__ import annotations

import json

from sre_agent.agents.deps import build_fake_deps
from sre_agent.agents.remediation import remediation_node
from sre_agent.core.llm_client import FakeLLMClient
from sre_agent.core.models import ActionType, ApprovalStatus
from sre_agent.graph.state import new_incident_state
from tests.factories import make_alert, make_incident


def _state(alert=None, incident=None):
    alert = alert or make_alert()
    incident = incident or make_incident(alert)
    return new_incident_state(alert, incident, token_budget=120_000)


def test_low_risk_restart_pod_auto_executes():
    action_json = json.dumps(
        {
            "actions": [
                {
                    "action_type": "restart_pod",
                    "target_resource": "checkout-7d9f-abc12",
                    "parameters": {},
                    "rationale": "crash loop, restart to clear it",
                }
            ]
        }
    )
    deps = build_fake_deps(llm=FakeLLMClient(default_response=action_json))
    state = _state()

    result = remediation_node(state, deps)

    assert len(result["executed_actions"]) == 1
    executed = result["executed_actions"][0]
    assert executed.action_type == ActionType.RESTART_POD
    assert executed.executed is True
    assert executed.execution_result is not None
    assert deps.approval_queue.list_pending() == []
    restart_calls = [c for c in deps.k8s.calls if c[0] == "restart_pod"]
    assert len(restart_calls) == 1


def test_rollback_deployment_goes_to_approval_queue_not_executed():
    action_json = json.dumps(
        {
            "actions": [
                {
                    "action_type": "rollback_deployment",
                    "target_resource": "checkout",
                    "parameters": {},
                    "rationale": "bad deploy suspected",
                }
            ]
        }
    )
    deps = build_fake_deps(llm=FakeLLMClient(default_response=action_json))
    state = _state()

    result = remediation_node(state, deps)

    assert result["executed_actions"] == []
    pending = deps.approval_queue.list_pending()
    assert len(pending) == 1
    assert pending[0].action.action_type == ActionType.ROLLBACK_DEPLOYMENT
    assert pending[0].action.approval_status == ApprovalStatus.PENDING
    # No k8s call should have happened for an action awaiting approval.
    assert deps.k8s.calls == []


def test_blocked_namespace_action_is_neither_executed_nor_queued():
    action_json = json.dumps(
        {
            "actions": [
                {
                    "action_type": "restart_pod",
                    "target_resource": "coredns-abc123",
                    "parameters": {},
                    "rationale": "coredns crash looping",
                }
            ]
        }
    )
    deps = build_fake_deps(llm=FakeLLMClient(default_response=action_json))
    alert = make_alert(namespace="kube-system", service="coredns")
    state = _state(alert=alert)

    result = remediation_node(state, deps)

    assert result["executed_actions"] == []
    assert deps.approval_queue.list_pending() == []
    blocked_events = [e for e in result["timeline"] if e.event_type == "action_blocked"]
    assert len(blocked_events) == 1


def test_platform_shared_namespace_always_requires_approval():
    action_json = json.dumps(
        {
            "actions": [
                {
                    "action_type": "restart_pod",
                    "target_resource": "shared-auth-abc123",
                    "parameters": {},
                    "rationale": "auth pod crash looping",
                }
            ]
        }
    )
    deps = build_fake_deps(llm=FakeLLMClient(default_response=action_json))
    alert = make_alert(namespace="platform-shared", service="shared-auth")
    state = _state(alert=alert)

    result = remediation_node(state, deps)

    assert result["executed_actions"] == []
    assert len(deps.approval_queue.list_pending()) == 1


def test_scale_replicas_exceeding_delta_threshold_requires_approval():
    action_json = json.dumps(
        {
            "actions": [
                {
                    "action_type": "scale_replicas",
                    "target_resource": "checkout",
                    "parameters": {"replicas": 10, "replica_delta": 5},
                    "rationale": "handle traffic spike",
                }
            ]
        }
    )
    deps = build_fake_deps(llm=FakeLLMClient(default_response=action_json))
    state = _state()

    result = remediation_node(state, deps)

    assert result["executed_actions"] == []
    assert len(deps.approval_queue.list_pending()) == 1


def test_scale_replicas_within_delta_threshold_auto_executes():
    action_json = json.dumps(
        {
            "actions": [
                {
                    "action_type": "scale_replicas",
                    "target_resource": "checkout",
                    "parameters": {"replicas": 4, "replica_delta": 2},
                    "rationale": "modest scale up",
                }
            ]
        }
    )
    deps = build_fake_deps(llm=FakeLLMClient(default_response=action_json))
    state = _state()

    result = remediation_node(state, deps)

    assert len(result["executed_actions"]) == 1


def test_invalid_action_type_from_llm_is_skipped():
    action_json = json.dumps(
        {
            "actions": [
                {
                    "action_type": "delete_namespace",  # not a real ActionType member
                    "target_resource": "checkout",
                    "parameters": {},
                    "rationale": "not a real action",
                }
            ]
        }
    )
    deps = build_fake_deps(llm=FakeLLMClient(default_response=action_json))
    state = _state()

    result = remediation_node(state, deps)

    assert result["proposed_actions"] == []
    assert result["executed_actions"] == []


def test_unparseable_llm_response_proposes_no_actions():
    deps = build_fake_deps(llm=FakeLLMClient(default_response="not valid json"))
    state = _state()

    result = remediation_node(state, deps)

    assert result["proposed_actions"] == []
    assert result["executed_actions"] == []
    assert result["next_agent"] == "communications"


def test_remediation_always_routes_to_communications():
    deps = build_fake_deps(llm=FakeLLMClient(default_response=json.dumps({"actions": []})))
    state = _state()

    result = remediation_node(state, deps)

    assert result["next_agent"] == "communications"
