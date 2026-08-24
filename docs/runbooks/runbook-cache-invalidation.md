# Cache Staleness and Invalidation Failures

service: catalog-svc
type: runbook
owner: catalog-team
last_reviewed: 2026-04-22

## Summary

`catalog-svc` fronts product/pricing reads with a Redis cache
(`catalog-cache`). Two distinct failure modes both show up as "wrong data
being served" and get confused with each other: (1) invalidation failure --
a write updates the source of truth but the corresponding cache key never
gets evicted, so reads keep returning stale data indefinitely; (2) cache
poisoning -- a bad value (e.g. from a bug in a backfill job or a race during
a deploy) gets written into the cache and served consistently until
flushed. Telling them apart determines whether the fix is a full flush or a
targeted key eviction.

## Symptoms

- Customer/support reports of stale prices, stock counts, or product
  descriptions that don't match what's in the admin/source-of-truth system.
- `catalog_cache_hit_ratio` stays abnormally high (>99%) even right after a
  known write -- a healthy invalidation path should show a brief dip in hit
  ratio for the specific key right after its write.
- No corresponding error rate spike -- this is a correctness incident, not
  an availability incident, which means it often surfaces from support
  tickets or a monitoring dashboard anomaly before Alertmanager fires
  anything.
- If poisoning is the cause, the incorrect value is usually identical across
  all reads for that key (a single bad write), versus invalidation failure
  where the value is *stale* (an older-but-once-correct value).

## Diagnosis

1. Reproduce against both cache and source of truth for a known-bad key:

   ```
   redis-cli -h catalog-cache.internal GET product:12345:price
   ```

   versus the value in the catalog primary database for the same product.
   A mismatch confirms a cache issue rather than a source-of-truth bug.

2. Check whether the write path actually attempted invalidation. Search
   application logs for the invalidation call tied to the write:

   ```
   grep "cache_invalidate key=product:12345" catalog-svc logs, last 2h
   ```

   Absence of this log line around the time of the known write points at an
   invalidation-path bug (the write succeeded, the eviction call never fired
   or failed silently). Presence of the log line with a mismatched cached
   value points at poisoning -- something re-wrote the wrong value into the
   cache after the correct invalidation.

3. Check for a recent deploy or backfill job around the time staleness
   started -- a batch job that writes directly to cache (bypassing the
   normal invalidation path) is the most common poisoning cause.

4. Check Redis-side signals for evictions or connectivity blips
   (`redis_evicted_keys_total`, `redis_connected_clients`) that might
   explain a partial invalidation failure distinct from an application bug.

## Remediation

**Targeted invalidation failure, small number of known keys**: manually
evict the specific affected keys rather than a full flush, if the
Diagnostic Agent can enumerate them from the write log. This keeps cache
hit ratio (and downstream DB load) healthy for the rest of the catalog.

**Widespread staleness, poisoning from a bad deploy/backfill, or the
affected key set can't be reliably enumerated**: propose `flush_cache` for
`catalog-cache`. This is allowlisted low-risk and auto-executes for a
single-target flush -- the tradeoff being accepted is a temporary spike in
DB read load and p99 latency as the cache repopulates from cold, in exchange
for correctness across every key rather than a best-effort partial
eviction.

**If the root cause is a backfill job bypassing the normal invalidation
path**, flushing the cache resolves the symptom but not the cause -- file
a follow-up for the catalog-team to route that job through the same
write-then-invalidate path as normal writes, or the same poisoning will
recur on the next run.

## Verification

- Spot-check the previously-stale keys against source of truth; values now
  match.
- `catalog_cache_hit_ratio` dips as expected immediately after the flush,
  then recovers to baseline over the following 10-15 minutes as the cache
  repopulates.
- DB read latency/QPS during the repopulation window stays within normal
  autoscaling headroom (watch `catalog-db` CPU and connection count during
  this window specifically).
