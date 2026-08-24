# Incident 001: Auto-restart worsened a genuine crash loop

Date: 2026-02-04
Severity of agent-service impact: medium (no data loss, no security impact,
degraded remediation effectiveness and briefly elevated downstream load)
Status: fixed

## What happened

`checkout-svc` pod `checkout-svc-7d9b4-x2plq` entered a genuine crash loop
(a null-pointer-style bug on a specific request shape, not a transient
startup issue) after a deploy. The Remediation Agent proposed `restart_pod`
for the single affected pod. `classify_action()` in
`src/sre_agent/guardrails/risk.py` classified it as low risk --
`restart_pod` starts at `RiskLevel.LOW` ("idempotent and scoped to a single
resource") and `replicas_affected` was 1, at the auto-execute ceiling
(`max_auto_replicas_affected: 1` in `guardrails/allowlist.yaml`) -- so it
auto-executed without approval.

The pod crashed again within seconds of the restart, on the same bug, and
the Remediation Agent proposed (and auto-executed) `restart_pod` again on
the next graph pass triggered by the follow-up alert. This repeated four
times over about six minutes before an on-call engineer, alerted by the
repeated Slack updates from the Communications Agent, intervened manually
and proposed a `rollback_deployment` instead, which resolved it.

## Impact

- Each restart reset the pod's connection-establishment backoff against
  `checkout-db` and downstream dependencies, so four restarts in six minutes
  produced four short bursts of reconnection load against those
  dependencies -- individually small, but a visible, avoidable spike in
  `checkout-db`'s connection-attempt rate during a window when the service
  was already degraded.
- No customer-facing outage beyond what the original crash loop was already
  causing, but six minutes of ineffective automated remediation delayed the
  actually-effective fix (rollback) by roughly that long, since the
  auto-executing low-risk path meant no human was pulled in earlier by an
  approval request.
- Repeated identical Slack "auto-executed restart_pod" messages added noise
  to the incident channel without new information, which is its own minor
  cost during an active incident.

## Root cause

The risk classifier's blast-radius signal for `restart_pod`
(`replicas_affected`) measured *how many pods* a single action instance
touches, which correctly captures the blast radius of *one* restart. It did
not account for *restart frequency* -- how many times the same pod (or the
same deployment) had already been restarted in a recent window. A single
pod restarted once is genuinely low-risk. The same pod restarted four times
in six minutes is a different, higher-risk situation: it's a strong signal
that the underlying problem is not something a restart can fix, and
continuing to auto-execute the same ineffective action has a real
(if modest) cost each time, with no corresponding benefit.

In short: the classifier treated `restart_pod` as always-low-risk based on
a point-in-time snapshot (this one action, this one pod count) rather than
incorporating recent history for the same target resource.

## Fix

Extended `classify_action()` in `guardrails/risk.py` to accept a
`recent_restart_count` signal (sourced from the same K8s pod status data
already available to the Diagnostic Agent, keyed on the target pod/
deployment) alongside the existing `replicas_affected` parameter, following
the same pattern already used for blast-radius escalation:

```
if recent_restart_count exceeds a configured threshold (N restarts within
M minutes for the same target_resource):
    level = RiskLevel.MEDIUM if level == RiskLevel.LOW else level
    reasons.append(f"{recent_restart_count} restarts in {M}m for "
                    f"{action.target_resource} exceeds auto-execute "
                    f"threshold; likely non-transient failure")
```

This hooks into the classifier exactly where the existing
`max_auto_replicas_affected` check does -- same function, same escalation
pattern (`RiskLevel.LOW` -> `RiskLevel.MEDIUM`, which forces
`requires_approval=True` via the existing `auto_execute_if_low_risk` gate),
so no change was needed to the approval queue, the audit log, or the
Communications Agent. The threshold (N restarts in M minutes) lives in
`allowlist.yaml`'s `action_policy.restart_pod` block alongside
`max_auto_replicas_affected`, so it's reviewable configuration, not a
hardcoded constant.

`runbook-pod-crashloop.md` was updated with explicit guidance: "if restart
frequency is high on a single pod... do not keep auto-restarting it,"
directly reflecting this incident.

## Follow-ups

1. Added the restart-frequency check to `risk.py` and `allowlist.yaml`
   (done, see Fix above).
2. Updated `runbook-pod-crashloop.md`'s remediation section to call out
   this specific escalation path (done).
3. Considered but did not implement: automatically converting a
   restart-frequency escalation directly into a `rollback_deployment`
   proposal rather than just an approval-gated `restart_pod`. Deferred
   because the Remediation Agent's action-selection prompt would need
   RCA context (is this deploy-correlated?) to make that judgment
   correctly, and forcing a specific action type from the risk classifier
   (rather than just gating the one already proposed) crosses a
   separation-of-concerns line between "propose" and "authorize" that's
   worth keeping distinct.
