# Incident 003: LLM provider outage stalled an active incident mid-graph

Date: 2026-05-30
Severity of agent-service impact: medium (one active sev2 incident's
automation stalled; on-call had to work it manually for part of the window)
Status: fixed, with a follow-up gap acknowledged

## What happened

Anthropic experienced a partial API outage (elevated 5xx and request
timeouts) for approximately 25 minutes. During that window, an active
`payments-svc` sev2 incident was mid-graph, sitting in the Diagnostic Agent
node waiting on an RCA call (`ModelTier.STRONG`) via
`deps.llm.complete(...)`. The call hung until the provider-side timeout,
then raised, and the graph run for that incident thread errored out of the
`diagnostic` node without a clean fallback -- no RCA hypotheses were
produced, and the run did not automatically retry or hand off to a
human-only path. The Communications Agent never ran for that incident
until on-call noticed independently (via the missing expected Slack update,
not via an explicit failure alert) about six minutes later and investigated.

A second, smaller incident that started later in the same outage window
triggered a Triage-stage LLM call that also failed; because Triage's
JSON-parse fallback (`except (json.JSONDecodeError, ValueError)` in
`agents/triage.py`) only handles a malformed *response*, not a call that
raises before returning one, that incident's graph run also errored rather
than degrading to a safe default severity.

## Impact

- The `payments-svc` sev2 incident had no automated RCA, no automated
  remediation proposal, and no Slack update for roughly 6 minutes until
  manual investigation started, and was worked entirely manually for the
  remainder -- automation added no value to that specific incident, though
  it also did not take any unsafe action.
- No remediation actions were incorrectly executed or skipped -- the
  failure mode was "the agent stopped helping," not "the agent did the
  wrong thing," which matters for severity assessment.
- No incident-thread state was lost: because the checkpointer persists
  state after every completed node write (`docs/adr/0001`), and the failure
  happened *during* the diagnostic node's LLM call rather than after a
  partial write, the checkpoint still reflected the last-good state
  (post-triage). This meant the graph run was safely resumable once the
  provider recovered, and the incident could have been re-driven from that
  checkpoint rather than needing to restart from scratch -- this was
  identified during the retro, not exploited live, since on-call had
  already resolved it manually by the time this was noticed.

## Root cause

`LLMClient.complete()` (`core/llm_client.py`) wraps the provider call in a
`try/except TimeoutError` that re-raises as `LLMTimeoutError`, but nothing
upstream of that call -- neither the individual agent nodes nor the graph
construction in `graph/build_graph.py` -- currently catches `LLMTimeoutError`
(or any exception from `complete()`) and converts it into a safe, terminal
state. An uncaught exception from a LangGraph node surfaces as a run error;
nothing routes that error to the Communications Agent for a "we couldn't
process this automatically, human review needed" message, unlike the
explicit `state.get("error")` check in `agents/supervisor.route()`, which
exists for exactly this purpose but was never being populated on this path
because no node was catching the exception and writing to it.

Separately: `tools/resilience.py`'s `CircuitBreaker` is used by the
Prometheus/Loki/K8s tool clients (each composes retries + timeout + circuit
breaker identically, per `ARCHITECTURE.md`'s failure-handling section), but
`LLMClient` does not currently use a `CircuitBreaker` at all -- every node
independently attempts its LLM call and independently times out, rather
than a shared breaker recognizing "the provider is down" after the first
failure and short-circuiting subsequent attempts across incidents during
the same outage window with a fast, cheap failure instead of a repeated
full-timeout wait.

## Fix

1. **Immediate**: `supervisor.route()`'s existing `if state.get("error"):
   return COMMUNICATIONS` check was already correct in design; the gap was
   that no node was populating `error` on an LLM failure. Added a
   try/except around each agent node's `deps.llm.complete(...)` call site
   that catches `LLMTimeoutError` and `LLMBudgetExceededError` specifically,
   writes a human-readable `error` string into state, and returns
   immediately rather than propagating the exception -- so `route()`'s
   existing error-handling edge now actually fires, and the Communications
   Agent posts an explicit "automated processing degraded, LLM provider
   issue, human review needed" update instead of the incident silently
   going quiet.

## Follow-up (acknowledged gap, not yet implemented)

2. **Wrap `LLMClient` calls in the same `CircuitBreaker` primitive already
   used by the tool clients.** The natural extension of
   `tools/resilience.py`'s existing pattern is a breaker keyed on
   `(provider,)` (or `(provider, tier)` if fast/strong should trip
   independently) around `_call_anthropic`/`_call_openai`, so that after N
   consecutive failures within an outage window, subsequent nodes across
   *any* active incident fail fast into the same `error`-state path above
   instead of each independently waiting out a full timeout. This is not
   yet implemented -- it requires deciding whether the breaker should be a
   process-global singleton (simplest, matches how tool-client breakers
   work today) or incident-scoped, and how that interacts with
   `CostLedger`'s per-incident token budget (a fast-failed call should not
   consume budget the way a timed-out one currently does, since a timed-out
   call may still be billed by the provider depending on how far the
   request got, whereas a breaker-rejected call never leaves the process).
3. Confirmed the checkpointer's resumability held up under this failure
   mode (see Impact) -- no change needed there, but this is now an explicit
   test case: `tests/unit` should include a scenario where a node's LLM
   call raises mid-graph and asserts the checkpoint from the prior node is
   still cleanly resumable afterward.
4. Add an explicit alert (`sre_agent_node_latency_seconds` combined with
   error-rate on LLM-calling nodes) so a provider outage affecting the
   agent-service itself pages the platform team directly, rather than
   relying on on-call noticing a missing Slack update as the first signal,
   which is what happened in this incident.
