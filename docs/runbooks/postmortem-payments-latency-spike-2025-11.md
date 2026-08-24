# Postmortem: payments-svc Latency Spike (2025-11-19)

service: payments-svc
type: postmortem
incident_id: INC-2025-1119-02
severity: sev2
author: payments-team
status: final

## Summary

On 2025-11-19, p99 latency on `payments-svc`'s authorization endpoint rose
from a baseline of ~180ms to over 4s for approximately 22 minutes, driven by
a downstream card-network sandbox misconfiguration that caused a small
percentage of authorization calls to hang until timeout instead of failing
fast. The incident did not reach outage-level error rates (most requests
still succeeded, just slowly), which delayed both automated and human
detection relative to a hard-failure incident of similar underlying
severity.

## Timeline (UTC)

- **09:14** -- A configuration change on the card-network integration
  partner's side (outside our control) begins causing a subset of
  authorization requests to hang rather than return quickly with an error.
- **09:15-09:26** -- p99 latency climbs gradually; no error-rate-based alert
  fires because the request eventually succeeds for most calls, just slowly
  -- only a latency-threshold alert would catch this, and the configured
  threshold at the time was tuned for p95, which stayed within bounds longer
  than p99 did.
- **09:27** -- `PaymentsHighLatencyP99` fires (a p99-specific alert added
  after a prior near-miss). Triage classifies sev2.
- **09:28** -- Diagnostic Agent correlates the latency signal with elevated
  request duration specifically on the authorization endpoint's downstream
  card-network call span, and retrieves no closely matching runbook (the
  RAG search against the runbook corpus returned only tangentially related
  results -- this specific failure mode, a slow-not-failing downstream
  dependency, was not yet documented). RCA hypothesis: downstream dependency
  degradation, confidence 0.62 -- correctly directionally right, but lower
  confidence than a well-documented scenario would have produced, which is
  itself informative.
- **09:30** -- Remediation Agent proposes no automated action -- no proposed
  action mapped cleanly to "a specific downstream dependency is slow but not
  failing," and correctly did not force-fit a `restart_pod` or similar
  action that wouldn't address a downstream cause. Routed to Communications
  with a status update noting downstream degradation and no automated
  remediation available.
- **09:33** -- On-call, informed via the Slack update, engages the payments
  integration on-call and confirms with the card-network partner that a
  config change on their end is the cause.
- **09:36** -- Payments-team applies a client-side timeout reduction
  (unrelated to this system's remediation actions -- a manual config change
  to `payments-svc`'s HTTP client timeout for that specific integration,
  deployed via the normal CD pipeline) so hanging calls fail fast and
  fall back to a retry against a secondary authorization path instead of
  blocking.
- **09:49** -- Card-network partner confirms their config issue is
  reverted.
- **09:51** -- p99 latency returns to baseline; incident closed after 15
  minutes of sustained normal latency.

## Root Cause

An upstream card-network partner's sandbox/gateway configuration change
caused a subset of authorization requests to hang rather than fail fast.
`payments-svc`'s HTTP client for that integration had no aggressive timeout
configured for this specific call path, so hanging requests consumed
connection-pool and worker capacity, degrading p99 latency for all
authorization traffic, not just the affected subset.

## Impact

- 22 minutes of degraded p99 latency (peak ~4s against a ~180ms baseline).
- No full outage -- overall authorization success rate stayed above 97%
  throughout, so this was a latency/experience incident rather than an
  availability incident, but a sev2 given payments' criticality and the
  magnitude of the p99 regression.

## What went well

- The p99-specific alert (added after an earlier near-miss, per its own
  history) caught this where a p95-only or error-rate-only alert would have
  missed it for considerably longer.
- The Diagnostic Agent correctly avoided over-claiming confidence when the
  RAG corpus had no closely matching runbook, and the Remediation Agent
  correctly proposed no action rather than forcing an ill-fitting one --
  both are the intended behavior when the system genuinely doesn't have a
  good automated answer.

## Action items

1. Add an aggressive, integration-specific client-side timeout for the
   card-network authorization call path as a permanent config (not just the
   incident-time manual change), with fast-fail-and-retry-secondary-path
   behavior as the default, not an incident response. (payments-team)
2. Write `runbook-slow-downstream-dependency.md` covering this class of
   failure (a dependency that degrades rather than fails) so future RAG
   retrieval has a documented match and RCA confidence is higher next time.
   (payments-team, tracked as a corpus gap)
3. Evaluate whether `flush_cache` or a scoped `restart_pod` against
   `payments-svc`'s connection pool would be a reasonable low-risk automated
   mitigation for "downstream call hanging, pool saturating" as a stopgap
   while the real fix (timeout tuning) is being identified -- currently no
   automated action exists for this scenario at all. (payments-team,
   platform-team)
4. Confirm p99-based latency alerting exists for all other services with
   an external payment/card-network dependency, not just payments-svc.
   (platform-team)
