# Database Connection Pool Exhaustion

service: checkout-svc
type: runbook
owner: checkout-team
last_reviewed: 2026-05-14

## Summary

`checkout-svc` holds a per-pod connection pool against the primary Postgres
instance (`checkout-db`). Under traffic spikes, a slow query, or a
partial-availability event on the database side, pods exhaust their local
pool faster than connections are returned, and every subsequent request
queues behind a full pool until it times out. This surfaces as a 5xx spike
that looks, at first glance, like a generic application failure -- the log
line is the fastest way to tell it apart from other causes.

## Symptoms

- 5xx rate on `checkout-svc` climbs sharply, usually within a couple of
  minutes, often correlated with a traffic ramp (flash sale, marketing push)
  or a slow-query deploy.
- Application logs show repeated `connection pool exhausted` or
  `TimeoutError: QueuePool limit of size 10 overflow 5 reached` lines.
- `pg_stat_activity` on `checkout-db` shows connection count near or at
  `max_connections`, with a large fraction in `idle in transaction` state
  (a strong signal of a query or transaction that isn't being closed
  promptly, not just raw volume).
- Latency (p99) for checkout endpoints that touch the DB rises well before
  the 5xx rate does -- if you catch it here, escalation to remediation is
  faster.

## Diagnosis

1. Confirm the 5xx spike and correlate with pool errors:

   ```
   sum(rate(http_requests_total{namespace="checkout",service="checkout-svc",status=~"5.."}[5m]))
   ```

   Cross-reference against log volume for `connection pool exhausted` over
   the same window -- if the two rise together, this runbook applies.

2. Check actual DB-side connection pressure, not just the app-side symptom:

   ```
   SELECT count(*), state FROM pg_stat_activity WHERE datname = 'checkout' GROUP BY state;
   ```

   A high `idle in transaction` count points at an application bug (a
   transaction opened and never committed/rolled back) rather than pure
   traffic volume -- that changes the fix from "scale out" to "find and kill
   the offending session, then file a bug."

3. Check for a recent deploy that changed query patterns or connection pool
   sizing (`SQLALCHEMY_POOL_SIZE`, `SQLALCHEMY_MAX_OVERFLOW` env vars) --
   a pool-size regression from a config change is a common root cause and
   means the fix is a rollback, not a scale-out.

4. Check per-pod connection usage against the configured pool size to
   confirm each pod is actually saturating its own pool rather than the
   database's global `max_connections` being the bottleneck (in which case
   scaling out replicas makes the problem *worse*, not better -- more pods
   means more total connections against the same DB ceiling).

## Remediation

**If per-pod pools are saturated but the database's global connection
headroom is fine**: propose `scale_replicas` to add pods, which reduces
per-pod connection pressure by spreading load across more, smaller pools.
This is allowlisted low-risk and auto-executes when the scale delta is
within `max_auto_scale_delta` (2, per `allowlist.yaml`) -- anything larger
requires approval so a human confirms the database has headroom for more
total connections first.

**If the database's global `max_connections` is the actual ceiling**,
scaling out replicas is the wrong remediation -- it adds connections against
a ceiling that's already the problem. Escalate to a human rather than
proposing `scale_replicas`; the fix is likely a pgbouncer/connection-pooler
tuning change or a `max_connections` increase, both outside the scope of
automated remediation.

**If `idle in transaction` sessions dominate**, this is an application bug,
not a capacity problem -- scaling won't fix it and will just spread the same
bug across more pods. Flag for the checkout-team to investigate the specific
code path holding transactions open; a `rollback_deployment` may be
appropriate if a specific deploy introduced the bug (see
`postmortem-checkout-outage-2026-03.md` for a case where this was exactly
the root cause).

## Verification

- 5xx rate returns to baseline (`< 0.1%`) within 10 minutes of remediation.
- `connection pool exhausted` log lines stop appearing.
- `pg_stat_activity` connection count stabilizes well under
  `max_connections` with `idle in transaction` count near zero.
