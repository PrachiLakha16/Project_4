# Regional Failover for payments-svc

service: payments-svc
type: runbook
owner: payments-team
last_reviewed: 2026-07-02

## Summary

`payments-svc` runs active/active across two AWS regions
(`us-east-1` primary, `us-west-2` standby-capacity secondary) fronted by
Route53 latency-based routing with health checks. A regional failover
(`trigger_failover`) shifts traffic weighting so the secondary region
absorbs the primary's load. This is the highest-blast-radius action this
system can ever propose -- it changes where every in-flight and new payment
request in the affected region is served from -- and it always requires
human approval regardless of computed risk. This runbook exists so that
approval, when it happens, happens fast and informed, not so the action
becomes auto-executable.

## Symptoms

- Region-wide degradation in `us-east-1`: elevated 5xx and timeout rates
  across payments-svc that don't correlate with any single deploy or
  dependency, but do correlate with AWS status-page signals or a broad
  connectivity/latency anomaly across multiple services in that region.
- Health check failures against the `us-east-1` payments-svc ALB target
  group persist for multiple consecutive check intervals (a single blip is
  not failover-worthy; sustained failure is).
- Downstream payment processor calls (card networks, ACH) from `us-east-1`
  specifically show elevated timeout rates while the same calls from
  `us-west-2` (if there's any secondary traffic already) do not -- this is
  the strongest signal that the problem is regional infrastructure, not the
  payment processor itself or a payments-svc code bug that would follow the
  traffic to the other region anyway.

## Diagnosis

1. Confirm the failure is regional in scope, not service- or
   dependency-specific:

   ```
   sum(rate(http_requests_total{namespace="payments",status=~"5..|0"}[5m])) by (region)
   ```

   If `us-west-2` is healthy while `us-east-1` is not, and the unhealthy
   signal spans multiple unrelated services (not just payments-svc), this
   supports a regional infrastructure cause and a failover response.

2. Rule out a payments-svc-specific bug that would simply follow traffic to
   the other region. Check recent deploy history for `payments-svc`
   specifically -- if a deploy went out shortly before symptoms started,
   this may be a `rollback_deployment` situation instead of (or in addition
   to) a regional failover.

3. Check AWS Health Dashboard / regional status for `us-east-1` to
   corroborate infrastructure-level cause versus coincidental timing.

4. Confirm `us-west-2` capacity and health are actually sufficient to absorb
   full production load before proposing failover -- a failover that trades
   a partial-region outage for a full-capacity outage in the secondary
   region is strictly worse.

## Remediation

`trigger_failover` is proposed with the target region and rationale, and is
**always** enqueued for approval -- there is no auto-execute path for this
action type in `guardrails/allowlist.yaml`, by design (see
`docs/adr/0003-human-approval-gate.md`). When reviewing an approval request
for this action:

- Confirm the diagnosis steps above were actually followed (the rationale
  field should reference regional-scope evidence, not just an elevated
  error rate that could be explained by something narrower).
- Confirm secondary-region capacity headroom before approving.
- If in doubt, reject and escalate to a live incident bridge rather than
  approving speculatively -- a failover is far easier to justify after
  confirming regional scope than to reverse cleanly once traffic has
  shifted.

After approval and execution, monitor both regions closely: `us-west-2` for
signs of being overwhelmed by the shifted load, and `us-east-1` for recovery
signals that would indicate it's safe to fail back.

## Verification

- Traffic-weighted 5xx/timeout rate for payments-svc returns to baseline
  within 5-10 minutes of the DNS weighting change propagating.
- `us-west-2` capacity metrics (CPU, connection pool, downstream processor
  latency) stay within normal operating range under the shifted load --
  don't declare success until this is confirmed, not just that
  `us-east-1`'s error rate dropped.
- Track `us-east-1` recovery separately; failing back is a distinct,
  separately-approved `trigger_failover` action, not automatic.
