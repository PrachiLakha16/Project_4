# Postmortem: checkout-svc Connection Pool Exhaustion Outage (2026-03-11)

service: checkout-svc
type: postmortem
incident_id: INC-2026-0311-01
severity: sev1
author: checkout-team
status: final

## Summary

On 2026-03-11, a deploy to `checkout-svc` introduced a code path that opened
a database transaction for every cart-pricing recalculation but failed to
commit or roll it back on one specific error branch. Under normal traffic
this leaked one connection every few minutes -- slow enough to go unnoticed
in staging. Under the elevated traffic of an active marketing promotion, the
leak rate was fast enough to exhaust each pod's connection pool within
roughly 40 minutes of the promotion starting, producing a full checkout
outage. Total customer-facing impact was approximately 34 minutes of
majority-failing checkout requests.

## Timeline (UTC)

- **14:02** -- `checkout-svc` deploy `v2026.03.11-4` completes rollout,
  containing the pricing-recalculation change with the transaction leak.
- **14:00** -- Scheduled marketing promotion begins, roughly tripling normal
  checkout traffic.
- **14:41** -- `checkout_requests_total{status=~"5.."}` crosses the sev1
  threshold; Alertmanager fires `CheckoutHighErrorRate`.
- **14:42** -- Triage Agent classifies sev1, routes to Diagnostic Agent.
- **14:43** -- Diagnostic Agent correlates the 5xx spike with `connection
  pool exhausted` log lines and retrieves
  `runbook-db-connection-pool-exhaustion.md` via RAG; RCA hypothesis:
  connection pool exhaustion, confidence 0.81. The agent's metric/log tools
  could not query `pg_stat_activity` directly, so this was flagged for
  on-call to check manually.
- **14:49 (diagnostic detail)** -- On-call ran the `pg_stat_activity` query
  by hand and confirmed a heavy `idle in transaction` presence, consistent
  with a transaction-leak root cause rather than pure traffic volume.
- **14:44** -- Remediation Agent proposes `scale_replicas` (delta +2); risk
  classifier auto-executes it as low-risk and within the auto-scale delta
  threshold.
- **14:47** -- Scale-out completes; error rate dips briefly but climbs again
  within 3 minutes as new pods hit the same leak.
- **14:49** -- On-call engineer, paged via the Communications Agent's Slack
  update, manually confirms `idle in transaction` sessions dominate
  `pg_stat_activity` and identifies the correlation with the 14:02 deploy.
- **14:52** -- On-call manually requests `rollback_deployment` to
  `v2026.03.11-3`; approved within 90 seconds.
- **15:00** -- Rollback completes, connection pool pressure clears within
  the following two minutes as leaked connections age out.
- **15:16** -- Error rate confirmed at baseline for 15 consecutive minutes;
  incident closed.

## Root Cause

A code change in `v2026.03.11-4` opened a DB transaction for cart-pricing
recalculation and had an error branch (a specific discount-code validation
failure) that returned early without committing or rolling back the
transaction. Under low traffic this leaked connections slowly enough to be
invisible; under promotion-level traffic, with a meaningfully higher rate of
invalid discount-code submissions than normal (many customers copy-pasting
expired promo codes), the leak rate was high enough to exhaust per-pod
connection pools within about 40 minutes.

The automated `scale_replicas` remediation was not wrong given the
information available at the time -- the symptom (5xx correlated with pool
exhaustion) genuinely matched that runbook's first remediation path, and the
per-pod-pressure signal the agent had access to didn't distinguish "too much
traffic for the pool" from "leaking regardless of traffic." It bought a few
minutes but could not have resolved the incident, because more pods just
meant more copies of the same leaking code path.

## Impact

- 34 minutes of majority-failing checkout requests (14:41-15:16, full
  outage roughly 14:41-15:00).
- Estimated cart abandonment during the window: significant, concentrated
  in the promotion's peak traffic period, compounding the revenue impact of
  the outage with lost promotional conversions specifically.

## What went well

- Triage and RCA correctly identified the connection-pool signature and
  surfaced the right runbook within 90 seconds of the alert firing.
- The auto-executed `scale_replicas` was a safe, low-risk action taken
  quickly, and while it didn't resolve the incident, it did not make things
  worse and cost no time waiting on approval.
- On-call's manual `pg_stat_activity` check (a step the agent's current tool
  set cannot perform directly) was the key diagnostic step and happened
  quickly because the Diagnostic Agent's RCA output pointed directly at
  that hypothesis.

## Action items

1. Add a direct `pg_stat_activity`-style query tool to the Diagnostic
   Agent's tool set so "idle in transaction count" becomes a first-class
   metric signal instead of requiring a manual on-call step. (checkout-team,
   platform-team)
2. Fix the discount-code validation error branch to close its transaction on
   every exit path; add a regression test specifically for the
   early-return-without-commit case. (checkout-team)
3. Add a connection-pool-leak-rate alert (rate of connection growth
   independent of traffic volume) so this class of issue pages before full
   exhaustion, not just at the exhaustion threshold. (platform-team)
4. Update `runbook-db-connection-pool-exhaustion.md`'s diagnosis section
   (done as part of this postmortem) to more strongly emphasize checking
   `idle in transaction` before proposing `scale_replicas`, since scaling
   out a leak makes the leak's aggregate rate worse, not better, even though
   it briefly reduces per-pod symptoms.
