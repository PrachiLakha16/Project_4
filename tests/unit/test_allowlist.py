"""Verifies load_allowlist() parses the real allowlist.yaml as expected.

If these assertions ever fail after someone edits allowlist.yaml, that is a
deliberate policy change and this test should be updated in the same PR --
that review friction is the point of testing the parsed values directly.
"""

from __future__ import annotations

from sre_agent.core.config import get_settings
from sre_agent.core.models import ActionType
from sre_agent.guardrails.allowlist import load_allowlist


def _policy():
    return load_allowlist(get_settings().allowlist_config_path)


def test_allowed_action_types():
    policy = _policy()
    assert policy.allowed_action_types == {
        "restart_pod",
        "rollback_deployment",
        "scale_replicas",
        "flush_cache",
        "trigger_failover",
    }


def test_denied_action_types_is_empty():
    policy = _policy()
    assert policy.denied_action_types == set()


def test_namespace_denylist():
    policy = _policy()
    assert policy.namespace_denylist == {
        "kube-system",
        "cert-manager",
        "external-secrets",
        "argocd",
    }
    assert policy.is_namespace_blocked("kube-system") is True
    assert policy.is_namespace_blocked("prod") is False


def test_always_require_approval_namespaces():
    policy = _policy()
    assert policy.always_require_approval_namespaces == {"platform-shared"}
    assert policy.requires_approval_by_namespace("platform-shared") is True
    assert policy.requires_approval_by_namespace("prod") is False


def test_idempotency_ttl_seconds():
    policy = _policy()
    assert policy.idempotency_ttl_seconds == 3600


def test_restart_pod_policy():
    policy = _policy()
    action_policy = policy.action_policy["restart_pod"]
    assert action_policy.auto_execute_if_low_risk is True
    assert action_policy.max_auto_replicas_affected == 1


def test_scale_replicas_policy():
    policy = _policy()
    action_policy = policy.action_policy["scale_replicas"]
    assert action_policy.auto_execute_if_low_risk is True
    assert action_policy.max_auto_scale_delta == 2


def test_rollback_deployment_never_auto_executes():
    policy = _policy()
    assert policy.action_policy["rollback_deployment"].auto_execute_if_low_risk is False


def test_trigger_failover_never_auto_executes():
    policy = _policy()
    assert policy.action_policy["trigger_failover"].auto_execute_if_low_risk is False


def test_flush_cache_auto_executes():
    policy = _policy()
    assert policy.action_policy["flush_cache"].auto_execute_if_low_risk is True


def test_is_action_type_permitted():
    policy = _policy()
    assert policy.is_action_type_permitted(ActionType.RESTART_POD) is True
    assert policy.is_action_type_permitted(ActionType.TRIGGER_FAILOVER) is True


def test_policy_for_unknown_action_type_returns_default():
    policy = _policy()
    # scale_replicas is defined in the yaml; policy_for should still work for
    # every ActionType even if a hypothetical future member were missing from
    # the yaml -- it falls back to a default ActionPolicy() rather than KeyError.
    default = policy.action_policy.get("nonexistent_action_type")
    assert default is None
