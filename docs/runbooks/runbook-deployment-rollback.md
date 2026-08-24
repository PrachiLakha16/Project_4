# Deployment Rollback Procedure

service: checkout-svc
type: runbook
owner: platform-team
last_reviewed: 2026-06-18

## Summary

A `rollback_deployment` reverts a Kubernetes `Deployment` to its previous
`ReplicaSet` revision. This is the standard response when a deploy is the
confirmed or strongly-suspected root cause of an incident and a simple
`restart_pod` hasn't cleared it (restarting doesn't help when every replica
is running the same bad revision). It always requires human approval --
unlike `restart_pod`, it changes the running pod template for every replica
at once, not just resets one pod's process state.

## Symptoms

- Elevated error rate, crash-looping, or latency regression starting within
  minutes of a deploy event for the affected service.
- The failure signature is consistent across most/all replicas, not
  isolated to one pod (isolated single-pod issues point at
  `runbook-pod-crashloop.md`'s "transient" path instead).
- `restart_pod` was already attempted (auto-executed or manually) and the
  same failure recurred within a few minutes on the restarted pod, because
  the new pod comes up running the same bad revision.

## Diagnosis

1. Confirm the deploy timeline against the incident start time:

   ```
   kubectl rollout history deployment/checkout-svc -n checkout
   ```

   Match the current revision's rollout timestamp against the first alert
   firing time. A gap of more than 15-20 minutes weakens the deploy-caused
   hypothesis; consider other causes first.

2. Diff the failing revision against the prior one for the actual change
   surface -- config, image tag, env vars, resource limits:

   ```
   kubectl diff -f <manifest> 2>/dev/null || kubectl get deployment checkout-svc -n checkout -o yaml
   ```

   This context matters for the postmortem even if the rollback itself
   doesn't require it -- knowing *what* changed narrows the follow-up fix.

3. Check logs across multiple replicas (not just one) to confirm the
   failure is deployment-wide:

   ```
   for p in $(kubectl get pods -n checkout -l app=checkout-svc -o name); do kubectl logs -n checkout $p --tail=20; done
   ```

   Consistent errors across replicas is the strongest confirmation that a
   rollback, not a restart, is the right action.

4. Identify the target rollback revision -- normally the immediately prior
   one, but confirm it was actually healthy (check its own incident history)
   before proposing it as the rollback target; rolling back to a revision
   that was itself mid-incident doesn't help.

## Remediation

Propose `rollback_deployment` with `target_resource` set to the deployment
name and `parameters` identifying the target revision. This action is
classified at least `RiskLevel.MEDIUM` unconditionally
(`_INTRINSICALLY_MEDIUM_RISK` in `guardrails/risk.py`) and always requires
approval (`auto_execute_if_low_risk: false` in `allowlist.yaml`) -- there is
no automated path to execution.

While the approval is pending:

- Do not also auto-execute `restart_pod` against the same pods repeatedly --
  if restarts already failed to clear the issue once, repeating them just
  adds restart-storm load without new information (see
  `docs/incidents/001-auto-restart-made-crashloop-worse.md`).
- The Communications Agent's Slack update should make clear that a rollback
  is pending approval and why, so on-call can approve quickly without
  re-deriving the diagnosis from scratch.

After approval, execution rolls the deployment back and the graph resumes
from the checkpoint captured when the action was enqueued (see
`docs/adr/0001-langgraph-over-raw-orchestration.md` on how this resumption
works).

## Verification

- `kubectl rollout status deployment/checkout-svc -n checkout` reports the
  rollback complete and all replicas ready.
- Error rate/latency for the affected endpoints returns to pre-deploy
  baseline within 5-10 minutes.
- No recurrence of the original failure signature in logs across all
  replicas for at least 15 minutes post-rollback.
- File a follow-up item for the owning team to fix-forward the underlying
  bug before attempting the deploy again -- a rollback resolves the incident,
  not the defect.
