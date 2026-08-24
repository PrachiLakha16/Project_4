"""Thorough coverage of guardrails.risk.classify_action.

classify_action is the gate between "an action was proposed" and "an action
executes automatically" -- it is the single highest-value piece of logic in
the guardrail stack, so it gets the most exhaustive test coverage in this
suite.
"""

from __future__ import annotations

from sre_agent.core.config import get_settings
from sre_agent.core.models import ActionType, RemediationAction, RiskLevel
from sre_agent.guardrails.allowlist import ActionPolicy, AllowlistPolicy, load_allowlist
from sre_agent.guardrails.risk import classify_action


def _action(
    action_type: ActionType, *, namespace: str = "prod", parameters: dict | None = None
) -> RemediationAction:
    return RemediationAction(
        action_type=action_type,
        target_cluster="prod-us-east-1",
        target_namespace=namespace,
        target_resource="checkout-7d9f-abc12",
        parameters=parameters or {},
        idempotency_key="idem-key-1",
        rationale="test rationale",
    )


def real_policy() -> AllowlistPolicy:
    return load_allowlist(get_settings().allowlist_config_path)


def test_low_risk_action_auto_executes():
    action = _action(ActionType.RESTART_POD)
    decision = classify_action(action, real_policy())
    assert decision.risk_level == RiskLevel.LOW
    assert decision.requires_approval is False
    assert decision.blocked is False


def test_rollback_deployment_always_requires_approval_medium_risk():
    action = _action(ActionType.ROLLBACK_DEPLOYMENT)
    decision = classify_action(action, real_policy())
    assert decision.risk_level == RiskLevel.MEDIUM
    assert decision.requires_approval is True
    assert decision.blocked is False


def test_trigger_failover_is_high_risk_and_requires_approval():
    action = _action(ActionType.TRIGGER_FAILOVER)
    decision = classify_action(action, real_policy())
    assert decision.risk_level == RiskLevel.HIGH
    assert decision.requires_approval is True
    assert decision.blocked is False
    assert any("high risk" in reason for reason in decision.reasons)


def test_blocked_namespace_short_circuits_to_high_and_blocked():
    action = _action(ActionType.RESTART_POD, namespace="kube-system")
    decision = classify_action(action, real_policy())
    assert decision.risk_level == RiskLevel.HIGH
    assert decision.requires_approval is True
    assert decision.blocked is True
    assert any("kube-system" in reason for reason in decision.reasons)


def test_denied_action_type_is_blocked():
    policy = AllowlistPolicy(
        allowed_action_types={"restart_pod"},
        denied_action_types={"trigger_failover"},
        namespace_denylist=set(),
        always_require_approval_namespaces=set(),
        action_policy={},
    )
    action = _action(ActionType.TRIGGER_FAILOVER)
    decision = classify_action(action, policy)
    assert decision.blocked is True
    assert decision.requires_approval is True
    assert "not in allowlist" in decision.reasons[0]


def test_action_type_not_in_allowed_list_is_blocked():
    policy = AllowlistPolicy(
        allowed_action_types={"restart_pod"},  # scale_replicas deliberately omitted
        denied_action_types=set(),
        namespace_denylist=set(),
        always_require_approval_namespaces=set(),
        action_policy={},
    )
    action = _action(ActionType.SCALE_REPLICAS)
    decision = classify_action(action, policy)
    assert decision.blocked is True


def test_always_require_approval_namespace_forces_approval_even_for_low_risk_action():
    action = _action(ActionType.RESTART_POD, namespace="platform-shared")
    decision = classify_action(action, real_policy())
    assert decision.risk_level == RiskLevel.MEDIUM
    assert decision.requires_approval is True
    assert decision.blocked is False
    assert any("always requires approval" in reason for reason in decision.reasons)


def test_scale_replicas_within_delta_threshold_auto_executes():
    action = _action(ActionType.SCALE_REPLICAS, parameters={"replica_delta": 2})
    decision = classify_action(action, real_policy())
    assert decision.risk_level == RiskLevel.LOW
    assert decision.requires_approval is False


def test_scale_replicas_delta_exceeding_threshold_requires_approval():
    action = _action(ActionType.SCALE_REPLICAS, parameters={"replica_delta": 5})
    decision = classify_action(action, real_policy())
    assert decision.risk_level == RiskLevel.MEDIUM
    assert decision.requires_approval is True
    assert any("exceeds auto-execute threshold" in reason for reason in decision.reasons)


def test_replicas_affected_exceeding_max_auto_replicas_bumps_risk():
    action = _action(ActionType.RESTART_POD)
    decision = classify_action(action, real_policy(), replicas_affected=3)
    assert decision.risk_level == RiskLevel.MEDIUM
    assert decision.requires_approval is True
    assert any("replicas affected exceeds" in reason for reason in decision.reasons)


def test_flush_cache_is_low_risk_and_auto_executes():
    action = _action(ActionType.FLUSH_CACHE)
    decision = classify_action(action, real_policy())
    assert decision.risk_level == RiskLevel.LOW
    assert decision.requires_approval is False


def test_low_risk_but_policy_disables_auto_execute_still_requires_approval():
    policy = AllowlistPolicy(
        allowed_action_types={"restart_pod"},
        denied_action_types=set(),
        namespace_denylist=set(),
        always_require_approval_namespaces=set(),
        action_policy={"restart_pod": ActionPolicy(auto_execute_if_low_risk=False)},
    )
    action = _action(ActionType.RESTART_POD)
    decision = classify_action(action, policy)
    assert decision.risk_level == RiskLevel.LOW
    assert decision.requires_approval is True
    assert decision.blocked is False
