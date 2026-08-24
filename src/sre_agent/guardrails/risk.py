"""Risk classification for proposed remediation actions.

This is the gate between "Remediation Agent proposed an action" and "action
executes automatically". It combines the static allowlist policy with
runtime blast-radius signals (how many pods/replicas affected, whether the
namespace is protected) to produce a RiskLevel and an approval decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from sre_agent.core.models import ActionType, RemediationAction, RiskLevel
from sre_agent.guardrails.allowlist import AllowlistPolicy


@dataclass
class RiskDecision:
    risk_level: RiskLevel
    requires_approval: bool
    reasons: list[str]
    blocked: bool = False


_INTRINSICALLY_HIGH_RISK = {ActionType.TRIGGER_FAILOVER}
_INTRINSICALLY_MEDIUM_RISK = {ActionType.ROLLBACK_DEPLOYMENT}


def classify_action(
    action: RemediationAction, policy: AllowlistPolicy, *, replicas_affected: int = 1
) -> RiskDecision:
    reasons: list[str] = []

    if not policy.is_action_type_permitted(action.action_type):
        return RiskDecision(
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            blocked=True,
            reasons=[f"action type {action.action_type.value} not in allowlist"],
        )

    if policy.is_namespace_blocked(action.target_namespace):
        return RiskDecision(
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            blocked=True,
            reasons=[f"namespace {action.target_namespace} is denylisted for automated actions"],
        )

    if action.action_type in _INTRINSICALLY_HIGH_RISK:
        level = RiskLevel.HIGH
        reasons.append(f"{action.action_type.value} is intrinsically high risk (wide blast radius)")
    elif action.action_type in _INTRINSICALLY_MEDIUM_RISK:
        level = RiskLevel.MEDIUM
        reasons.append(f"{action.action_type.value} changes the running pod template")
    else:
        level = RiskLevel.LOW
        reasons.append(f"{action.action_type.value} is idempotent and scoped to a single resource")

    action_policy = policy.policy_for(action.action_type)
    if (
        action_policy.max_auto_replicas_affected is not None
        and replicas_affected > action_policy.max_auto_replicas_affected
    ):
        level = RiskLevel.MEDIUM if level == RiskLevel.LOW else level
        reasons.append(
            f"{replicas_affected} replicas affected exceeds auto-execute threshold of {action_policy.max_auto_replicas_affected}"
        )

    if action.action_type == ActionType.SCALE_REPLICAS:
        delta = abs(int(action.parameters.get("replica_delta", 0)))
        if (
            action_policy.max_auto_scale_delta is not None
            and delta > action_policy.max_auto_scale_delta
        ):
            level = RiskLevel.MEDIUM
            reasons.append(
                f"scale delta {delta} exceeds auto-execute threshold of {action_policy.max_auto_scale_delta}"
            )

    if policy.requires_approval_by_namespace(action.target_namespace):
        level = RiskLevel.MEDIUM if level == RiskLevel.LOW else level
        reasons.append(f"namespace {action.target_namespace} always requires approval")

    requires_approval = level != RiskLevel.LOW or not action_policy.auto_execute_if_low_risk
    return RiskDecision(risk_level=level, requires_approval=requires_approval, reasons=reasons)
