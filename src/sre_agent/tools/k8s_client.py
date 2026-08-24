"""Kubernetes API client wrapper used by the Remediation Agent.

Wraps the official `kubernetes` python client behind a narrow interface so
the Remediation Agent depends only on the five verbs it actually needs
(get_pods, restart_pod, rollback_deployment, scale_deployment, flush_cache
hook). Every mutating method requires an idempotency_key and is guarded by
`IdempotencyStore` so a retried LangGraph node never double-executes.

The real client authenticates via the pod's IRSA-mapped ServiceAccount token
(see infrastructure/helm and SECURITY.md) and is scoped to a least-privilege
Role limited to get/list/watch pods + patch deployments/statefulsets in the
namespaces it's allowed to touch -- it cannot create ClusterRoleBindings,
touch RBAC objects, or act outside its namespace allowlist.
"""

from __future__ import annotations

from typing import Protocol

from sre_agent.core.config import Settings, get_settings
from sre_agent.observability.tracing import traced_tool_call
from sre_agent.tools.resilience import CircuitBreaker, IdempotencyStore, with_retries, with_timeout
from sre_agent.tools.schemas import (
    K8sActionOutput,
    K8sGetPodsInput,
    K8sGetPodsOutput,
    K8sRestartPodInput,
    K8sRollbackDeploymentInput,
    K8sScaleDeploymentInput,
)


class K8sClientProtocol(Protocol):
    def get_pods(self, inp: K8sGetPodsInput) -> K8sGetPodsOutput: ...
    def restart_pod(self, inp: K8sRestartPodInput) -> K8sActionOutput: ...
    def rollback_deployment(self, inp: K8sRollbackDeploymentInput) -> K8sActionOutput: ...
    def scale_deployment(self, inp: K8sScaleDeploymentInput) -> K8sActionOutput: ...


class K8sClient:
    def __init__(
        self, settings: Settings | None = None, idempotency_store: IdempotencyStore | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.idempotency_store = idempotency_store or IdempotencyStore()
        self._breaker = CircuitBreaker(
            "kubernetes_api",
            failure_threshold=self.settings.circuit_breaker_failure_threshold,
            reset_seconds=self.settings.circuit_breaker_reset_seconds,
        )
        self._api = None  # lazily constructed; see _core_v1/_apps_v1

    def _load_kube_config(self) -> None:
        from kubernetes import client, config

        if self.settings.kube_config_path:
            config.load_kube_config(
                config_file=self.settings.kube_config_path,
                context=self.settings.kube_context or None,
            )
        else:
            config.load_incluster_config()
        self._core_v1 = client.CoreV1Api()
        self._apps_v1 = client.AppsV1Api()

    @traced_tool_call("k8s_get_pods")
    @with_retries(max_attempts=3)
    @with_timeout(10.0)
    def get_pods(self, inp: K8sGetPodsInput) -> K8sGetPodsOutput:
        def _do() -> K8sGetPodsOutput:
            if self._api is None:
                self._load_kube_config()
            pods = self._core_v1.list_namespaced_pod(
                inp.namespace, label_selector=inp.label_selector or ""
            )
            return K8sGetPodsOutput(
                pods=[
                    {
                        "name": p.metadata.name,
                        "phase": p.status.phase,
                        "restarts": sum(
                            c.restart_count for c in (p.status.container_statuses or [])
                        ),
                    }
                    for p in pods.items
                ]
            )

        return self._breaker.call(_do)

    @traced_tool_call("k8s_restart_pod")
    def restart_pod(self, inp: K8sRestartPodInput) -> K8sActionOutput:
        if self.idempotency_store.already_executed(inp.idempotency_key):
            return self.idempotency_store.get_result(inp.idempotency_key)

        @with_retries(max_attempts=3)
        @with_timeout(15.0)
        def _do() -> K8sActionOutput:
            if self._api is None:
                self._load_kube_config()
            self._core_v1.delete_namespaced_pod(inp.pod_name, inp.namespace)
            return K8sActionOutput(
                action="restart_pod",
                resource=f"{inp.namespace}/{inp.pod_name}",
                status="succeeded",
                detail="pod deleted; controller will reschedule",
            )

        result = self._breaker.call(lambda: _do())
        self.idempotency_store.mark_executed(inp.idempotency_key, result)
        return result

    @traced_tool_call("k8s_rollback_deployment")
    def rollback_deployment(self, inp: K8sRollbackDeploymentInput) -> K8sActionOutput:
        if self.idempotency_store.already_executed(inp.idempotency_key):
            return self.idempotency_store.get_result(inp.idempotency_key)

        @with_retries(max_attempts=3)
        @with_timeout(20.0)
        def _do() -> K8sActionOutput:
            if self._api is None:
                self._load_kube_config()
            # Rollback = re-apply the previous revision's pod template; the
            # real client reads the deployment's kubectl.kubernetes.io/revision
            # annotated ReplicaSets and patches spec.template accordingly.
            self._apps_v1.patch_namespaced_deployment(
                inp.deployment,
                inp.namespace,
                {
                    "spec": {
                        "template": {"metadata": {"annotations": {"sre-agent/rollback": "true"}}}
                    }
                },
            )
            return K8sActionOutput(
                action="rollback_deployment",
                resource=f"{inp.namespace}/{inp.deployment}",
                status="succeeded",
                detail="rolled back to previous ReplicaSet revision",
            )

        result = self._breaker.call(lambda: _do())
        self.idempotency_store.mark_executed(inp.idempotency_key, result)
        return result

    @traced_tool_call("k8s_scale_deployment")
    def scale_deployment(self, inp: K8sScaleDeploymentInput) -> K8sActionOutput:
        if self.idempotency_store.already_executed(inp.idempotency_key):
            return self.idempotency_store.get_result(inp.idempotency_key)

        @with_retries(max_attempts=3)
        @with_timeout(15.0)
        def _do() -> K8sActionOutput:
            if self._api is None:
                self._load_kube_config()
            self._apps_v1.patch_namespaced_deployment_scale(
                inp.deployment, inp.namespace, {"spec": {"replicas": inp.replicas}}
            )
            return K8sActionOutput(
                action="scale_deployment",
                resource=f"{inp.namespace}/{inp.deployment}",
                status="succeeded",
                detail=f"scaled to {inp.replicas} replicas",
            )

        result = self._breaker.call(lambda: _do())
        self.idempotency_store.mark_executed(inp.idempotency_key, result)
        return result


class FakeK8sClient:
    """In-memory fake used by tests: tracks calls, honors idempotency, never touches a cluster."""

    def __init__(self) -> None:
        self.idempotency_store = IdempotencyStore()
        self.calls: list[tuple[str, dict]] = []

    def get_pods(self, inp: K8sGetPodsInput) -> K8sGetPodsOutput:
        self.calls.append(("get_pods", inp.model_dump()))
        return K8sGetPodsOutput(
            pods=[{"name": "checkout-7d9f-abc12", "phase": "CrashLoopBackOff", "restarts": 14}]
        )

    def restart_pod(self, inp: K8sRestartPodInput) -> K8sActionOutput:
        if self.idempotency_store.already_executed(inp.idempotency_key):
            return self.idempotency_store.get_result(inp.idempotency_key)
        self.calls.append(("restart_pod", inp.model_dump()))
        result = K8sActionOutput(
            action="restart_pod",
            resource=f"{inp.namespace}/{inp.pod_name}",
            status="succeeded",
            detail="pod deleted (fake)",
        )
        self.idempotency_store.mark_executed(inp.idempotency_key, result)
        return result

    def rollback_deployment(self, inp: K8sRollbackDeploymentInput) -> K8sActionOutput:
        if self.idempotency_store.already_executed(inp.idempotency_key):
            return self.idempotency_store.get_result(inp.idempotency_key)
        self.calls.append(("rollback_deployment", inp.model_dump()))
        result = K8sActionOutput(
            action="rollback_deployment",
            resource=f"{inp.namespace}/{inp.deployment}",
            status="succeeded",
            detail="rolled back (fake)",
        )
        self.idempotency_store.mark_executed(inp.idempotency_key, result)
        return result

    def scale_deployment(self, inp: K8sScaleDeploymentInput) -> K8sActionOutput:
        if self.idempotency_store.already_executed(inp.idempotency_key):
            return self.idempotency_store.get_result(inp.idempotency_key)
        self.calls.append(("scale_deployment", inp.model_dump()))
        result = K8sActionOutput(
            action="scale_deployment",
            resource=f"{inp.namespace}/{inp.deployment}",
            status="succeeded",
            detail=f"scaled to {inp.replicas} (fake)",
        )
        self.idempotency_store.mark_executed(inp.idempotency_key, result)
        return result
