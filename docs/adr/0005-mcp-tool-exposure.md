# 0005: Expose only the read-only tool subset over MCP; never expose mutating tools

## Status

Accepted.

## Context

The system already wraps every external dependency (Prometheus, Loki,
Kubernetes, Slack, the RAG retriever) behind a typed client with a
`Protocol` interface and a `Fake*` counterpart
(`src/sre_agent/tools/*_client.py`). Once those clients exist, exposing them
over MCP (`src/sre_agent/mcp_server/server.py`) so an engineer's own
MCP-capable chat client can query them directly during an incident -- without
waiting on the agent graph -- is close to free: `register_tools()` just
wraps each client method in an `@mcp.tool()` decorator.

The question is *which* tools to expose. The full tool layer includes
mutating operations: `k8s.restart_pod`, `k8s.rollback_deployment`,
`k8s.scale_deployment` (used by `agents/remediation.py`'s
`_execute_action()`). If those were exposed over MCP identically to the
read-only ones, any MCP client with network access to the server could
restart a pod or roll back a deployment directly -- completely bypassing
`guardrails/allowlist.py`, `guardrails/risk.py`, and the approval queue,
because none of those guardrails live in the tool client itself; they live
in `agents/remediation.py`, the one call site that currently invokes mutating
tools.

## Decision

`register_tools()` in `mcp_server/server.py` registers exactly four
read-only tools:

- `query_prometheus(promql)` -> `prometheus.instant_query(...)`
- `query_logs(namespace, service, since_minutes, filter_text)` ->
  `logs.query_logs(...)`
- `get_pods(namespace, label_selector)` -> `k8s.get_pods(...)` (pod
  phase/restart-count listing -- explicitly called out in the tool docstring
  as read-only)
- `search_runbooks(query, top_k, service_filter)` ->
  `retriever.search(...)` (RAG over the runbook/postmortem corpus)

`restart_pod`, `rollback_deployment`, and `scale_deployment` are
deliberately **not** registered as MCP tools, anywhere, under any name.
There is no configuration flag that turns them on. The only call site for
mutating K8s actions in the entire codebase is
`agents/remediation._execute_action()`, which only runs after
`classify_action()` has produced a decision and, for anything not
auto-executable, only after `ApprovalQueue.decide(approved=True, ...)` has
been called by an `sre-oncall`-scoped principal (`api/rbac.py`).

This means the guardrail chain (allowlist -> risk classification -> approval
gate -> execution) has exactly one entry point into mutating infrastructure.
MCP exposure was evaluated as a potential second entry point and rejected
specifically because a second entry point is a bypass by definition, no
matter how well-intentioned the exposed tool's docstring is.

## Consequences

**Positive**

- An engineer working an incident by hand (their own chat client, not this
  agent) gets the investigative value of the tool layer -- querying metrics,
  tailing logs, listing pods, searching runbooks -- without the system
  having to trust that MCP client's judgment about when a mutation is safe.
- The guardrail model in ADR 0003 stays true: "remediation actions are
  authorized by the allowlist + risk classifier + approval gate, not by
  anything the LLM decides on its own" (`SECURITY.md`) holds regardless of
  which LLM or client is asking, because the mutating path simply doesn't
  exist outside the Remediation Agent's code.
- Adding a new read-only tool (e.g. a future OTel trace-query tool,
  referenced as a likely extension in `agents/diagnostic.py`'s docstring) is
  low-risk to expose over MCP by the same pattern, because "read-only" is
  the actual bar, not "whatever exists in `tools/`."

**Negative / tradeoffs**

- The MCP server and the LangGraph Remediation Agent now have two different
  answers to "can this system take an action," which engineers extending the
  tool layer need to understand: a new client method is not automatically
  MCP-exposed just because it exists, and a new mutating capability must
  never be added to `register_tools()` even for convenience (e.g. "just for
  debugging in the MCP client during an incident") -- that temptation is the
  main risk this ADR exists to head off.
- This is a code-review-enforced boundary, not a technical one: nothing
  currently stops a future PR from calling `mcp.tool()` on
  `k8s.restart_pod`. The mitigation is that this ADR and the MCP server's own
  module docstring both state the constraint explicitly, and it should be an
  explicit checklist item in review for any change to
  `mcp_server/server.py` or the tool client Protocols it depends on.
