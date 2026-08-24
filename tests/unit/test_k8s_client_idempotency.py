"""Tests that FakeK8sClient honors idempotency_key across retried calls."""

from __future__ import annotations

from sre_agent.tools.k8s_client import FakeK8sClient
from sre_agent.tools.schemas import (
    K8sRestartPodInput,
    K8sRollbackDeploymentInput,
    K8sScaleDeploymentInput,
)


def test_restart_pod_same_idempotency_key_executes_once():
    fake_k8s = FakeK8sClient()
    inp = K8sRestartPodInput(
        namespace="prod", pod_name="checkout-7d9f-abc12", idempotency_key="idem-restart-1"
    )

    result1 = fake_k8s.restart_pod(inp)
    result2 = fake_k8s.restart_pod(inp)

    restart_calls = [c for c in fake_k8s.calls if c[0] == "restart_pod"]
    assert len(restart_calls) == 1
    assert result1 == result2


def test_restart_pod_different_idempotency_keys_execute_separately():
    fake_k8s = FakeK8sClient()
    inp1 = K8sRestartPodInput(namespace="prod", pod_name="pod-a", idempotency_key="key-a")
    inp2 = K8sRestartPodInput(namespace="prod", pod_name="pod-b", idempotency_key="key-b")

    fake_k8s.restart_pod(inp1)
    fake_k8s.restart_pod(inp2)

    restart_calls = [c for c in fake_k8s.calls if c[0] == "restart_pod"]
    assert len(restart_calls) == 2


def test_rollback_deployment_idempotency():
    fake_k8s = FakeK8sClient()
    inp = K8sRollbackDeploymentInput(
        namespace="prod", deployment="checkout", idempotency_key="idem-rollback-1"
    )

    result1 = fake_k8s.rollback_deployment(inp)
    result2 = fake_k8s.rollback_deployment(inp)

    calls = [c for c in fake_k8s.calls if c[0] == "rollback_deployment"]
    assert len(calls) == 1
    assert result1 == result2


def test_scale_deployment_idempotency():
    fake_k8s = FakeK8sClient()
    inp = K8sScaleDeploymentInput(
        namespace="prod", deployment="checkout", replicas=5, idempotency_key="idem-scale-1"
    )

    result1 = fake_k8s.scale_deployment(inp)
    result2 = fake_k8s.scale_deployment(inp)

    calls = [c for c in fake_k8s.calls if c[0] == "scale_deployment"]
    assert len(calls) == 1
    assert result1 == result2
    assert result1.status == "succeeded"


def test_idempotency_store_is_shared_and_reflects_execution():
    fake_k8s = FakeK8sClient()
    inp = K8sRestartPodInput(
        namespace="prod", pod_name="checkout-7d9f-abc12", idempotency_key="idem-restart-shared"
    )

    assert fake_k8s.idempotency_store.already_executed("idem-restart-shared") is False
    fake_k8s.restart_pod(inp)
    assert fake_k8s.idempotency_store.already_executed("idem-restart-shared") is True
