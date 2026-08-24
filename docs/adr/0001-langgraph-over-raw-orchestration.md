# 0001: Use LangGraph for agent orchestration instead of a hand-rolled orchestrator or pure prompt-chaining

## Status

Accepted.

## Context

The system routes an incoming alert through four specialist agents (Triage,
Diagnostic/RCA, Remediation, Communications), and the path is not linear:
Triage can short-circuit straight to Communications (suppressed/duplicate/
injection-flagged alerts), and Remediation can pause a run indefinitely when
a proposed action lands in the approval queue -- resuming only when
`POST /approvals/{action_id}` records a human decision, possibly hours later
(see `src/sre_agent/api/routes/approvals.py` and
`src/sre_agent/guardrails/approval_queue.py`).

Three approaches were considered:

1. **Pure prompt-chaining** -- one long prompt (or a fixed sequence of calls)
   that asks the model to "decide what to do next" in free text. Rejected:
   there is no way to durably pause mid-chain waiting on an external human
   decision, no structured place to enforce `VALID_TRANSITIONS`
   (`src/sre_agent/agents/supervisor.py`), and testing a specific handoff
   (e.g. "prompt-injection-flagged alerts must reach Communications without
   touching Diagnostic or Remediation") means asserting on model output
   rather than on a state machine.
2. **A hand-rolled orchestrator** -- a Python `while` loop with an
   `if next_agent == ...` dispatch table and our own persistence layer for
   pausing/resuming. This is roughly what LangGraph provides, minus years of
   edge-case handling around checkpointing, parallel-write reducers, and
   replay -- and we would own that code.
3. **LangGraph** (chosen) -- an explicit state graph (`IncidentState` in
   `src/sre_agent/core/models.py`) with conditional edges resolved by
   `route()` in `supervisor.py`, and a pluggable `BaseCheckpointSaver`
   (`src/sre_agent/graph/checkpointer.py`) that persists the full node-by-node
   state after every write.

## Decision

Build the incident-response pipeline as a LangGraph `StateGraph` over
`IncidentState`, with the supervisor's `route()` function -- not an LLM call
-- deciding the next node from the `next_agent` field each specialist writes.
`assert_valid_transition()` enforces the same `VALID_TRANSITIONS` table the
graph is wired from, so an invalid handoff (e.g. Diagnostic routing directly
to Communications) fails loudly in `tests/unit/test_supervisor_routing.py`
rather than silently producing a wrong Slack message.

The decisive factor is **resumability across the approval gate**: every
`graph.invoke()` is scoped to `thread_id = incident_id`
(`checkpointer.thread_config()`). When Remediation enqueues an action instead
of auto-executing it, the synchronous portion of that run ends, but the full
`IncidentState` -- timeline, RCA hypotheses, proposed actions, everything --
is already durably checkpointed. When a human calls the approvals endpoint,
the graph resumes from exactly that checkpoint instead of the API needing to
reconstruct incident context from scratch or keep a process alive in memory
for however long an on-call engineer takes to look at Slack.

List-valued state fields (`timeline`, `metric_signals`, `log_signals`,
`trace_signals`, `executed_actions`) use `Annotated[..., operator.add]`
reducers, and `metadata` uses a dict-merge reducer, so that when multiple
node writes touch the same run, they accumulate instead of one overwriting
another -- this is a LangGraph-specific mechanism we get for free instead of
hand-writing merge logic in an orchestrator loop.

## Consequences

**Positive**

- Durable pause/resume at the approval gate is the framework's core value
  proposition here, not a bolted-on feature -- an incident can sit pending
  approval for `APPROVAL_QUEUE_STALE_MINUTES` (default 15, configurable) or
  longer before `expire_stale()` gives up on it, and the graph state survives
  a pod restart in between because it's in Postgres, not process memory.
- Conditional routing lives in one reviewable, unit-testable function
  (`route()`) instead of being implicit in prompt wording.
- OpenTelemetry spans wrap each node via `traced_node()`
  (`observability/tracing.py`), giving per-node latency
  (`sre_agent_node_latency_seconds`) without instrumenting a custom loop.

**Negative / tradeoffs**

- Adds a framework dependency (`langgraph`, `langgraph.checkpoint.postgres`)
  the team has to track through version upgrades, on top of the LLM SDKs
  already abstracted by `LLMClient`.
- Learning curve for engineers unfamiliar with LangGraph's reducer/channel
  model -- the `Annotated[list[...], operator.add]` pattern is not obvious
  the first time you see a duplicate timeline entry from a re-run node.
- The state schema (`IncidentState` as a `TypedDict`) is somewhat rigid:
  every node's return dict must match keys LangGraph knows how to merge, so
  adding a new piece of per-incident state means touching the shared schema
  in `core/models.py` rather than a node owning private state. We accept this
  because the alternative (untyped ad hoc state) is worse for a system with
  audit and postmortem requirements.
- Graph replay/checkpointing semantics mean a node must be safe to consider
  re-entrant with respect to its own side effects; this is why remediation
  actions carry an `idempotency_key` (see ADR 0003) rather than relying on
  "the graph only runs a node once."
