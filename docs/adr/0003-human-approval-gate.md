# 0003: Medium- and high-risk remediation actions always route through a human approval gate

## Status

Accepted.

## Context

The Remediation Agent can take real, mutating actions against production
infrastructure: restarting a pod, rolling back a deployment, scaling
replicas, flushing a cache, or triggering a regional failover
(`ActionType` in `src/sre_agent/core/models.py`). An LLM proposes these
actions (`_ACTION_SYSTEM_PROMPT` in `agents/remediation.py`), but an LLM
proposal is not, by itself, a safe basis for executing something with real
blast radius -- the model can hallucinate a target resource, misjudge
severity, or (see `docs/incidents/002-prompt-injection-attempt.md`) be
working from attacker-influenced input in the first place.

Full autonomy (execute anything the model proposes) was rejected outright:
there is no bound on blast radius, and a bad `rollback_deployment` or
`trigger_failover` is exactly the kind of action a human wants to have eyes
on before it happens, not after. Full manual-only (never auto-execute
anything) was also rejected: the entire point of the system is to remove
the mechanical, low-stakes toil (see README "The problem") of an on-call
engineer restarting a single crash-looping pod at 3am -- gating *everything*
on a human defeats that.

## Decision

Every proposed `RemediationAction` passes through `classify_action()`
(`src/sre_agent/guardrails/risk.py`), which combines the static allowlist
policy (`guardrails/allowlist.yaml`) with runtime blast-radius signals to
produce a `RiskLevel` and a `requires_approval` boolean:

- **Action-type ceiling**: `allowlist.yaml`'s `allowed_action_types` is the
  full set of actions the agent may ever propose; `namespace_denylist`
  (`kube-system`, `cert-manager`, `external-secrets`, `argocd`) blocks
  automated action entirely regardless of risk, and any action there is
  marked `blocked=True` and audited, never enqueued for approval as if it
  might be approvable.
- **Intrinsic risk by action type**: `trigger_failover` is always
  `RiskLevel.HIGH` (`_INTRINSICALLY_HIGH_RISK`); `rollback_deployment` is
  always at least `RiskLevel.MEDIUM` (`_INTRINSICALLY_MEDIUM_RISK`) because
  it changes the running pod template, not just replica count. Both, per
  `action_policy` in `allowlist.yaml`
  (`auto_execute_if_low_risk: false` on each), **always** require approval --
  there is no risk level at which they auto-execute.
- **Blast-radius escalation for the auto-executable actions**:
  `restart_pod`, `scale_replicas`, and `flush_cache` start at
  `RiskLevel.LOW` ("idempotent and scoped to a single resource"), but
  `classify_action()` escalates them to `MEDIUM` if `replicas_affected`
  exceeds `max_auto_replicas_affected` (1, for `restart_pod`) or if a
  `scale_replicas` action's `replica_delta` exceeds `max_auto_scale_delta`
  (2). A namespace in `always_require_approval_namespaces`
  (`platform-shared`) forces at least `MEDIUM` regardless of the action.
- **The auto-execute condition** is `risk_level == LOW AND
  action_policy.auto_execute_if_low_risk AND not blocked` -- all three, not
  any one. Everything else (`requires_approval=True`) goes to
  `deps.approval_queue.enqueue()` instead of `_execute_action()`, which:
  records a `PendingApproval` with the classifier's `reasons`, is visible via
  `GET /approvals` and the Slack notification the Communications Agent
  posts, and is only resolved by an `sre-oncall`-scoped
  `POST /approvals/{action_id}` call (`api/rbac.py`) -- see ADR-adjacent
  reasoning in `docs/THREAT_MODEL.md` on why Slack itself is
  notification-only, not an authorization surface.

## Consequences

**Positive**

- The blast-radius bound is enforced in code the classifier owns
  (`risk.py`), not in prompt instructions the model could ignore or
  misinterpret -- `rollback_deployment` and `trigger_failover` cannot reach
  execution without a human decision no matter what the LLM's rationale
  text says.
- Low-stakes toil (a single crash-looping pod restart) is genuinely
  automated, which is where most of the MTTR reduction comes from, per the
  README's use cases.
- `docs/incidents/001-auto-restart-made-crashloop-worse.md` documents a real
  gap found in the *blast-radius* dimension of this design (restart
  frequency wasn't originally a signal) and how the same `risk.py`
  mechanism was extended to close it -- the gate is a living control, not a
  one-time decision.

**Negative / tradeoffs**

- Approval-gated actions add latency to incident resolution proportional to
  on-call responsiveness, bounded above by `APPROVAL_QUEUE_STALE_MINUTES`
  before `expire_stale()` marks the action `EXPIRED` (not executed, not
  silently dropped -- an explicit terminal state that still shows up in the
  postmortem).
- The thresholds (`max_auto_replicas_affected=1`,
  `max_auto_scale_delta=2`) are static configuration, not learned or
  incident-specific -- a genuinely low-risk `scale_replicas` by 3 replicas
  in a well-understood, over-provisioned service still requires approval
  because the classifier can't currently express "this service's headroom
  makes a delta of 3 as safe as 2 usually is." Tuning that requires editing
  `allowlist.yaml`, which is deliberately a reviewable, versioned file rather
  than agent-adjustable state.
