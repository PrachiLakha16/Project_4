"""Loader for the action allowlist/denylist policy (allowlist.yaml)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from sre_agent.core.models import ActionType


@dataclass
class ActionPolicy:
    auto_execute_if_low_risk: bool = False
    max_auto_replicas_affected: int | None = None
    max_auto_scale_delta: int | None = None


@dataclass
class AllowlistPolicy:
    allowed_action_types: set[str]
    denied_action_types: set[str]
    namespace_denylist: set[str]
    always_require_approval_namespaces: set[str]
    action_policy: dict[str, ActionPolicy]
    idempotency_ttl_seconds: int = 3600

    def is_action_type_permitted(self, action_type: ActionType) -> bool:
        value = action_type.value
        if value in self.denied_action_types:
            return False
        return value in self.allowed_action_types

    def is_namespace_blocked(self, namespace: str) -> bool:
        return namespace in self.namespace_denylist

    def requires_approval_by_namespace(self, namespace: str) -> bool:
        return namespace in self.always_require_approval_namespaces

    def policy_for(self, action_type: ActionType) -> ActionPolicy:
        return self.action_policy.get(action_type.value, ActionPolicy())


def load_allowlist(path: str | Path) -> AllowlistPolicy:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    action_policy = {
        name: ActionPolicy(
            auto_execute_if_low_risk=bool(cfg.get("auto_execute_if_low_risk", False)),
            max_auto_replicas_affected=cfg.get("max_auto_replicas_affected"),
            max_auto_scale_delta=cfg.get("max_auto_scale_delta"),
        )
        for name, cfg in (raw.get("action_policy") or {}).items()
    }
    return AllowlistPolicy(
        allowed_action_types=set(raw.get("allowed_action_types") or []),
        denied_action_types=set(raw.get("denied_action_types") or []),
        namespace_denylist=set(raw.get("namespace_denylist") or []),
        always_require_approval_namespaces=set(raw.get("always_require_approval_namespaces") or []),
        action_policy=action_policy,
        idempotency_ttl_seconds=int(raw.get("idempotency_ttl_seconds", 3600)),
    )
