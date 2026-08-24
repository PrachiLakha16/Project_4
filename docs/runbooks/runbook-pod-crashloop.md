# Pod CrashLoopBackOff Response

service: checkout-svc
type: runbook
owner: checkout-team
last_reviewed: 2026-06-01

## Summary

`checkout-svc` pods enter `CrashLoopBackOff` most often after a deploy
introduces a startup-time error (bad config, missing env var, a migration
that hasn't run yet) or after a downstream dependency (payments-svc,
inventory-svc) starts rejecting connections hard enough that the readiness
probe never passes. This runbook covers triage and the two remediation
paths: a plain restart when the crash looks transient, and a rollback when
it doesn't.

## Symptoms

- Alertmanager fires `KubePodCrashLooping` for one or more pods in
  `checkout-svc`.
- `kubectl get pods -n checkout` shows `RESTARTS` climbing over a short
  window, `STATUS` cycling `CrashLoopBackOff` -> `Running` -> `Error`.
- Checkout success rate (`checkout_requests_total{status="success"}`) drops
  in step with the restart count.
- On-call gets paged within minutes of a deploy completing -- check the
  deploy event timeline first, this is the single strongest correlating
  signal.

## Diagnosis

1. Confirm scope and recency of the crash loop:

   ```
   sum(increase(kube_pod_container_status_restarts_total{namespace="checkout",container="checkout-svc"}[15m])) by (pod)
   ```

   A handful of restarts across many pods over 15 minutes is very different
   from one pod restarting 10+ times -- the former often means a shared
   dependency is down, the latter often means that specific pod's node or
   local state is the problem.

2. Pull the crash reason and last exit code:

   ```
   kubectl describe pod -n checkout <pod-name> | grep -A5 "Last State"
   ```

   Exit code 1 with a stack trace in the last few log lines is almost always
   an application-level startup failure. Exit code 137 (OOMKilled) is a
   memory problem, not a crash loop in the classic sense -- see the resource
   limits section of the on-call wiki, not this runbook.

3. Check logs for the specific failure signature:

   ```
   kubectl logs -n checkout <pod-name> --previous | tail -100
   ```

   Common patterns: `connection refused` to a dependency's ClusterIP
   (downstream outage, not a checkout-svc bug), `KeyError` / `config key not
   found` (bad or missing config from the deploy), `relation "..." does not
   exist` (migration ordering issue -- deploy ran ahead of the DB migration
   job).

4. Correlate against the deploy timeline. If the first crash timestamp is
   within a couple of minutes of a `checkout-svc` deploy event, treat this as
   deploy-caused until proven otherwise.

## Remediation

**If the crash looks transient** (isolated pod, no clear deploy
correlation, exit code suggests a one-off like a slow dependency timeout on
startup): `restart_pod` on the affected pod. This is allowlisted as
low-risk and auto-executes when only a single replica is affected
(`max_auto_replicas_affected: 1` in `guardrails/allowlist.yaml`) -- most
single-pod crash loops resolve this way without paging anyone.

**If restarts don't clear it, or the crash is clearly deploy-correlated**
(config/schema error, present across most/all replicas of the deployment):
propose `rollback_deployment` to the last known-good revision. This always
requires human approval regardless of computed risk (it changes the running
pod template, not just pod state) -- confirm the rollback target revision
before approving, not just that "a rollback" is proposed.

**If restart frequency is high on a single pod** (more than a handful of
restarts within a short window), do not keep auto-restarting it -- a repeated
restart resets the backoff timer and can add load to a downstream dependency
each time the pod re-initializes its connections. Escalate to a
`rollback_deployment` proposal instead of continuing to restart. See
`docs/incidents/001-auto-restart-made-crashloop-worse.md` for the incident
that motivated this guidance.

**If the cause is a downstream dependency**, do not remediate checkout-svc
at all -- follow that dependency's own runbook (e.g.
`runbook-db-connection-pool-exhaustion.md` if the log signature points at
the database) and let checkout-svc recover on its own once the dependency
is healthy again.

## Verification

After remediation, confirm:

- `kube_pod_container_status_restarts_total` for the affected pod(s) has
  flattened for at least 10 minutes.
- Checkout success rate has returned to baseline (`> 99.5%` over a 5-minute
  window).
- No new `CrashLoopBackOff` alerts fire for the same pods within the next
  30 minutes.
