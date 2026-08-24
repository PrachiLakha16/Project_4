# Staff/Principal Interview Q&A: sre-incident-agent

These questions probe the actual design decisions in this codebase, not
generic agentic-AI trivia. Each answer is grounded in specific files and
functions so it can be defended against follow-up questions about the real
implementation.

---

### 1. How do you prevent the remediation agent from taking a destructive action twice on retry?

Every `RemediationAction` carries an `idempotency_key`
(`core/models.py`), computed deterministically in
`agents/remediation.py`'s `_idempotency_key()`:

```python
hashlib.sha256(f"{incident_id}:{action_type}:{target_resource}".encode()).hexdigest()[:24]
```

Because it's a pure function of `(incident_id, action_type,
target_resource)` rather than a random UUID, the same logical action
proposed twice for the same incident and target -- whether from a LangGraph
replay, a retried tool call after a network blip, or the agent proposing the
same fix twice across graph passes -- produces the identical key. The key is
passed through to the K8s client call (`K8sRestartPodInput(...,
idempotency_key=...)`), and `tools/resilience.py`'s `IdempotencyStore`
checks `already_executed(key)` before allowing execution; if it's already
been executed, the cached `execution_result` is returned instead of
re-running the mutation. Production backs this with a Postgres table with a
unique constraint (per the `IdempotencyStore` docstring) so the guarantee
survives process restarts, not just in-process retries. The
`idempotency_ttl_seconds` (3600, in `allowlist.yaml`) bounds how long a key
is considered "recent" so a legitimately-needed repeat action (e.g. the same
pod crash-looping again a day later) isn't permanently blocked.

---

### 2. How would you scale the approval queue to handle 50 concurrent incidents?

The current `ApprovalQueue` (`guardrails/approval_queue.py`) is explicitly
documented as in-memory with production backed by Postgres -- "the interface
here is what the FastAPI layer and tests use, so swapping the backing store
doesn't ripple outward." At 50 concurrent incidents, in-memory breaks down
for two reasons: it doesn't survive a pod restart, and it doesn't work
across horizontally-scaled FastAPI replicas (each replica would have its own
queue, so an approval decision on replica B wouldn't be visible to a
webhook/GET request served by replica A). The fix is a Postgres-backed
`pending_approvals` table (mirroring `audit_log`'s pattern in
`guardrails/audit.py`) with an index on `incident_id` and
`approval_status`, so `list_pending()` stays fast. `expire_stale()` becomes
a periodic scheduled sweep (a cron job or a scheduled Lambda/K8s CronJob)
querying `WHERE approval_status = 'pending' AND requested_at < now() -
interval`, rather than something invoked ad hoc. For a responsive UI, I'd
add Postgres `LISTEN/NOTIFY` or a lightweight Redis pub/sub layer so an
approval dashboard gets push updates instead of polling `GET /approvals`.
None of this changes the `ApprovalQueue` interface -- `enqueue`/`decide`/
`get`/`list_pending`/`expire_stale` stay the contract; only the backing
store changes, which is exactly what the existing docstring anticipates.

---

### 3. Walk through what happens if the LLM provider has an outage during an active incident.

`LLMClient.complete()` (`core/llm_client.py`) wraps the provider call and
converts a network-level timeout into `LLMTimeoutError`; a budget-exhausted
incident raises `LLMBudgetExceededError` before any call is even attempted.
As built today, neither exception is caught inside the agent nodes
themselves -- it propagates up out of the LangGraph node. This was a real
gap surfaced in `docs/incidents/003-llm-provider-outage-mid-incident.md`:
the fix was to catch both exceptions at each node's LLM call site and write
a human-readable `error` into `IncidentState`, so `supervisor.route()`'s
existing `if state.get("error"): return COMMUNICATIONS` check actually
fires and a human gets an explicit "automated processing degraded" Slack
update instead of the incident silently going quiet.

The extension I'd make next is wrapping `LLMClient`'s provider calls in the
same `CircuitBreaker` primitive `tools/resilience.py` already uses for
Prometheus/Loki/K8s clients -- currently `LLMClient` has no breaker, so
every node independently waits out a full timeout during an outage instead
of failing fast after the first few failures. That requires deciding
whether the breaker is a process-global singleton (matching the tool-client
pattern) and how a fast-failed call interacts with `CostLedger`'s
per-incident token budget (a breaker-rejected call should record zero
tokens, unlike a timed-out one that may have partially completed
provider-side).

Because the Postgres checkpointer persists state after every completed
node write (ADR 0001), and the failure happens mid-call rather than after a
partial state write, the checkpoint reflects the last fully-completed node.
So the graph run is safely resumable from that checkpoint once the provider
recovers -- either automatically (if the error-handling path retriggers the
node) or by a human re-driving the thread.

---

### 4. Why is severity classification on the fast/cheap model tier and RCA on the strong tier?

`ModelTier.FAST` and `ModelTier.STRONG` map to `LLM_MODEL_FAST` (
`claude-haiku-4-5`) and `LLM_MODEL_STRONG` (`claude-sonnet-5`) respectively
(`.env.example`, `core/llm_client.py`). Triage's job (`agents/triage.py`)
is a narrow, low-ambiguity classification task -- map alert metadata to one
of four severities with a one-sentence rationale, strict JSON output, 200
max tokens -- and it has to complete comfortably inside the time-to-triage
SLO (`TIME_TO_TRIAGE` histogram, `observability/metrics.py`) since it's the
very first LLM call in every graph run. There's little reasoning depth to
buy by spending more on this call.

RCA (`agents/diagnostic.py`) is explicitly called out in its own module
docstring as "the highest-leverage part of this system" -- it has to
synthesize correlated Prometheus/Loki signals and RAG-retrieved runbook
excerpts into ranked hypotheses with confidence scores and supporting
evidence, which is genuinely harder reasoning where model quality
differences show up in output quality. Paying strong-tier cost/latency once
per incident for the RCA call, versus fast-tier for the (more frequent,
lower-stakes) triage/comms/planning calls, is the right cost/quality
tradeoff given `INCIDENT_TOKEN_BUDGET` is a hard per-incident ceiling either
way.

---

### 5. How does the system prevent prompt injection in alert annotations from causing a real action?

Two independent layers. First, `guardrails/prompt_injection.py` scans every
`annotations`/`labels` value against `_SUSPICIOUS_PATTERNS` (phrases like
"ignore previous instructions," "widen ... permission," embedded
`<system>`/`<assistant>` tags) before Triage's LLM call; a match sets
`incident.severity = SEV2` and routes straight to Communications
(`next_agent = "communications"`), skipping Diagnostic and Remediation
entirely, with `metadata.prompt_injection_suspected = True` recorded for
audit visibility. Even unflagged text is wrapped in an
`<untrusted_external_data>` block instructing the model to treat it as data.

Second, and more importantly per `SECURITY.md`'s own framing -- this is
"defense in depth, not the primary control" -- even if injected text reached
a prompt unflagged and the model acted on it, there is no path from "the
LLM says do X" to X actually happening for anything with real blast radius.
Every proposed action is risk-classified against `guardrails/allowlist.yaml`
and, for anything not low-risk-and-allowlisted, requires a human decision
via the RBAC-protected `/approvals/{action_id}` endpoint. And the action
space itself is constrained to five `ActionType` values -- "widen an IAM
role" or "run arbitrary code" is not a representable action regardless of
what the model outputs. `docs/incidents/002-prompt-injection-attempt.md` is
a documented real occurrence of exactly this scenario.

---

### 6. Why does the checkpointer make thread_id = incident_id, and what does that buy you operationally?

`checkpointer.thread_config(incident_id)` returns
`{"configurable": {"thread_id": incident_id}}`, and every `graph.invoke()`/
`graph.stream()` call for that incident uses it. This means LangGraph's
checkpoint history *is* the incident's state history -- there's no separate
data model to keep in sync. Operationally this buys: (1) resumability -- a
run paused at the approval gate resumes from exactly the last node write
when a decision arrives, potentially hours later, without the API needing
any incident-specific reconstruction logic; (2) natural debuggability -- to
inspect "what did the graph know at each step of INC-abc123," you query the
checkpointer by that exact thread_id, no join or lookup table required; (3)
isolation -- one incident's state can never leak into another's merge
reducers, because reducers only combine writes within a single thread_id's
checkpoint chain. The tradeoff is that thread_id becomes a load-bearing
identifier end to end (webhook -> `IncidentRecord.incident_id` -> graph
invoke -> approval queue's `incident_id` field -> audit log's
`incident_id`), so it has to be generated once, early, and threaded
consistently -- which is exactly what `IncidentRecord`'s
`default_factory=lambda: f"INC-{uuid4().hex[:10]}"` and the rest of the
pipeline do.

---

### 7. How would you extend this to multi-cluster/multi-region incidents?

`RawAlert` already carries both `cluster` and `namespace` fields
independently of `service`, and `RemediationAction` carries
`target_cluster` separately from `target_namespace`/`target_resource`, so
the data model doesn't need to change for a single incident to reference
resources in multiple clusters. The bigger changes: (1) the K8s tool client
(`tools/k8s_client.py`) currently implies a single configured
`KUBE_CONTEXT`; multi-cluster requires it to accept a `target_cluster`
parameter and resolve credentials per cluster (likely via distinct IRSA
roles per cluster, keeping the least-privilege boundary from `SECURITY.md`
intact per cluster rather than one role spanning all of them); (2)
`allowlist.yaml`'s namespace-scoped policy would need a cluster dimension
too -- `namespace_denylist` and `always_require_approval_namespaces` should
arguably become `(cluster, namespace)` pairs, since "platform-shared" might
mean something different per cluster; (3) the Diagnostic Agent's Prometheus/
Loki queries would need to fan out across per-region instances (or a
federated/global query layer) and merge results, rather than the current
single-endpoint `PROMETHEUS_BASE_URL`/`LOKI_BASE_URL`; (4) `trigger_failover`
already exists as an `ActionType` specifically for the regional case (see
`docs/runbooks/runbook-regional-failover.md`) and stays intrinsically
high-risk/always-approval, which is the right posture to keep regardless of
how many regions exist.

---

### 8. What's the blast-radius reasoning behind the allowlist.yaml policy for each action type?

`restart_pod`: scoped to one pod's process state, no data loss, trivially
reversible (the pod comes back up). Ceiling: `max_auto_replicas_affected: 1`
-- a restart touching more than one replica at once stops being "one
isolated pod issue" and starts looking like a systemic problem, escalated
to `MEDIUM`. `scale_replicas`: additive/subtractive capacity change, also
reversible, but a large delta changes cost and cluster capacity
meaningfully -- `max_auto_scale_delta: 2` bounds how much capacity change
happens without a human sanity-check. `flush_cache`: idempotent and
service-scoped, but "auto_execute_if_low_risk: true" accepts a temporary
latency/DB-load cost during cache repopulation as the tradeoff for
correctness, which is judged safe to automate. `rollback_deployment`:
intrinsically `MEDIUM` unconditionally (`_INTRINSICALLY_MEDIUM_RISK`)
because it changes the running pod template for every replica at once --
higher blast radius than a restart, and if the rollback target itself
turns out to be unhealthy, that's a new, harder-to-diagnose problem, so a
human always confirms the target revision first.
`trigger_failover`: intrinsically `HIGH` (`_INTRINSICALLY_HIGH_RISK`) -- it
redirects traffic for an entire region, the largest blast radius the system
can express, and always requires approval with no auto-execute path at any
risk level, matching `docs/runbooks/runbook-regional-failover.md`'s
guidance to reject-and-escalate rather than approve speculatively when in
doubt.

---

### 9. How do you test agent behavior deterministically given LLM non-determinism?

Every LLM-calling component is written against `LLMClient`'s interface, not
a concrete provider SDK (ADR 0004), which means tests substitute
`FakeLLMClient` (`core/llm_client.py`). It takes a `responder: dict[str,
str]` mapping a substring found in the prompt to a canned JSON response,
checked in insertion order, with a `default_response` fallback -- so a test
can assert "given this exact alert payload, Triage classifies sev1" by
scripting the fast-tier response for that prompt shape, with zero network
calls and zero API keys required. Critically, `FakeLLMClient.complete()`
still records synthetic token counts (`len(prompt)//4`, `len(text)//4`)
through the same `CostLedger` real calls use, so budget-enforcement logic
(`LLMBudgetExceededError`) is exercised identically in tests as in
production, not bypassed. The same pattern extends to every other external
dependency -- `tools/*_client.py` each have a `Fake*` counterpart behind a
shared `Protocol` -- so a full graph run in a test exercises real routing
logic (`supervisor.route()`, `VALID_TRANSITIONS`) and real guardrail logic
(`classify_action()`, allowlist checks) against fully deterministic
inputs. `evaluation/run_eval.py` uses the same `Fake*` clients to score a
golden dataset in CI, which is what makes RCA/action-selection quality
regressions catchable without live model calls.

---

### 10. How would you add a new remediation action type safely?

Five places, in order: (1) add the value to `ActionType` in `core/models.py`
-- this is the schema boundary every other layer trusts; (2) add it to
`allowlist.yaml`'s `allowed_action_types` and decide its `action_policy`
(does it ever auto-execute, and under what blast-radius ceiling?) --
default to `auto_execute_if_low_risk: false` until there's a documented,
reviewed reason to allow automation, mirroring how `rollback_deployment`
and `trigger_failover` are treated; (3) decide whether it belongs in
`_INTRINSICALLY_HIGH_RISK`/`_INTRINSICALLY_MEDIUM_RISK` in `risk.py` --
most new action types should start there rather than defaulting to LOW,
since LOW is reserved for actions proven safe over time (`restart_pod`,
`scale_replicas`, `flush_cache`); (4) implement the actual execution in
`agents/remediation.py`'s `_execute_action()`, calling into a new or
existing tool client method with the same `idempotency_key` pattern; (5)
update `agents/remediation.py`'s `_ACTION_SYSTEM_PROMPT` so the planning
LLM call knows the new action type exists and when to propose it. Never
skip step 5 by having the model propose actions that don't exist -- the
`try: action_type = ActionType(raw["action_type"]) except (KeyError,
ValueError): continue` guard in `remediation_node` silently drops anything
that doesn't parse, which is the correct safe-default behavior but means an
unlisted action type is just quietly never proposed, not an error you'd
notice. And critically: never add the new action's tool client method to
`mcp_server/server.py`'s `register_tools()` if it mutates anything -- see
ADR 0005.

---

### 11. What's the failure mode if Postgres (checkpointer + pgvector) goes down mid-incident?

Two distinct impacts, per ADR 0002's own tradeoff discussion. Checkpoint
writes fail: any in-flight graph run can't persist its next state
transition, meaning a node that completes but can't checkpoint is at risk
of being re-run from the last successful checkpoint on retry/resume
(intentional -- this is exactly why idempotency keys exist, see Q1) rather
than silently losing work. A pending approval that can't be recorded/looked
up means `GET /approvals` and `POST /approvals/{action_id}` degrade or fail
until Postgres recovers -- incidents already past that point stay stuck
mid-flight rather than resolving, which is the direct cost of using the
same instance for checkpointing (ADR 0002 acknowledges "a Postgres outage
stalls both new checkpoint writes and RAG retrieval simultaneously, where a
split architecture would degrade RAG independently of graph execution").
RAG retrieval fails: `PgVectorStore.similarity_search()` raises, which
propagates out of `RunbookRetriever.search()` into the Diagnostic Agent's
node -- currently there's no fallback to a cached/stale runbook set, so RCA
quality degrades to "no retrieved context" at best or the node errors
entirely, which is the same class of gap identified for LLM outages in Q3
and should get the same treatment (catch, degrade to reduced-confidence
RCA or route to human, don't propagate an uncaught exception). The
mitigation posture is standard Postgres HA (read replica, automated
failover, connection pool retry with backoff already used elsewhere via
`tools/resilience.py`'s pattern) rather than anything special to this
system's design.

---

### 12. How do you keep the RAG corpus from going stale or being poisoned?

Poisoning is addressed at ingestion time, not retrieval time: `rag/ingest.py`
only reads `docs/runbooks/*.md` from this repository's own filesystem --
there is no live, user-submitted, or agent-writable ingestion path. Every
document in the corpus reaches it through the same PR review process as any
other code change (per ADR 0002/THREAT_MODEL's Information Disclosure row),
so the trust boundary is "reviewed and merged," identical to trusting any
other file in the repo. Staleness is a process problem more than a
technical one: `ingest_directory()` re-embeds and `upsert()`s on every run
(keyed by `chunk_id`, `ON CONFLICT ... DO UPDATE`), so re-running ingestion
after editing a runbook is how staleness gets fixed -- but nothing currently
triggers that automatically on a runbook merge. The natural fix is a CI step
on merge to `docs/runbooks/**` that runs `python -m sre_agent.rag.ingest`
against the production pgvector instance, turning "someone remembers to
re-ingest" into "merging the PR re-ingests." I'd also add a periodic audit
comparing `runbook_chunks.metadata->>'source_path'` against what's actually
in `docs/runbooks/` to catch orphaned chunks from deleted/renamed docs,
since `ingest_directory()` only upserts, it never prunes.

---

### 13. Why a namespaced Role instead of a ClusterRole for the K8s ServiceAccount?

Because the agent's legitimate operating surface is a known, bounded set of
namespaces -- the ones running the services it's meant to remediate -- not
the whole cluster. A `ClusterRole` would grant the same `get/list/watch`
pods and `patch` deployments/scale permissions cluster-wide, including in
namespaces like `kube-system`, `cert-manager`, `external-secrets`, and
`argocd` that `allowlist.yaml`'s `namespace_denylist` explicitly says the
Remediation Agent should never touch. With a `ClusterRole`, that denylist
would be the *only* thing standing between the agent and, say, patching a
`cert-manager` deployment -- a single bug in `is_namespace_blocked()` or a
misconfigured `allowlist.yaml` would be a cluster-wide compromise, not a
contained one. With a namespaced `Role` bound only in the allowed
namespaces, the denylist and the RBAC binding are two independent layers
that both have to fail for the agent to touch a protected namespace -- the
Kubernetes API server itself rejects a `patch` against a namespace the
`RoleBinding` doesn't cover, regardless of what the application code
decided. This is the same defense-in-depth principle as the IAM role having
no `iam:*` permissions: don't rely on application logic alone to enforce a
boundary the platform can enforce for you.

---

### 14. How would you reason about cost at scale (100s of incidents/day) given the per-incident token budget design?

`INCIDENT_TOKEN_BUDGET` (120,000 by default) and `CostLedger`
(`observability/cost.py`) already give a hard per-incident ceiling and
per-call USD tracking (`_PRICING_PER_1K` list prices, illustrative but
structurally correct) via `CostEntry.usd`. At 100s of incidents/day, the
aggregate cost is a sum over incidents, most of which (per the README's
"noisy/flapping alert" and dedup paths) never reach a full Diagnostic/RCA
pass -- Triage-only or suppressed/duplicate incidents cost one fast-tier
call, not the full pipeline, so the actual cost distribution is dominated
by genuine sev1-3 incidents that reach RCA. I'd add: (1) a
`sre_agent_llm_cost_usd_total` metric (currently `LLM_TOKENS_USED` tracks
tokens by model/direction but not USD directly) so cost is visible in
Grafana alongside `sre_agent_incidents_triaged_total`, letting you correlate
cost with incident volume/severity mix over time; (2) a daily/monthly
aggregate budget alert distinct from the per-incident cutoff, since 100s of
incidents each safely under their individual budget can still sum to an
unexpectedly large daily bill; (3) revisiting whether Communications'
postmortem-drafting call (only fires for sev1/sev2, `FAST` tier, 1000 max
tokens) is worth its cost versus a template-based draft for lower-severity-
adjacent cases, since it's one of the more marginal LLM calls in the
pipeline relative to its value.

---

### 15. How do you validate that the supervisor's routing logic is correct, given it's the safety-critical dispatch point?

The design choice in ADR 0001 is precisely to make this validatable: the
supervisor is not an LLM call, it's the deterministic `route()` function in
`agents/supervisor.py`, reading `state["next_agent"]` and returning it (or
`COMMUNICATIONS` if `state.get("error")` is set). Because it's plain Python
over a `TypedDict`, `tests/unit/test_supervisor_routing.py` can construct
arbitrary `IncidentState` values directly -- no LLM, no mocking needed --
and assert `route(state) == expected_node` for every case: normal
sev1-3 triage, sev4/suppressed/duplicate/injection-flagged short-circuits,
and the error-override path. `assert_valid_transition(from_node, to_node)`
is the second, independent check -- it validates a proposed handoff against
`VALID_TRANSITIONS` and raises on anything not explicitly allowed (e.g. if
a future bug had Diagnostic try to route directly to Communications,
bypassing Remediation), so the graph construction in `build_graph.py` can
call it at each edge to fail loudly in CI rather than silently accept an
unintended shortcut. Because `next_agent` is state written by each
specialist node (not inferred from prompt text), a bug in Triage's routing
decision shows up as a wrong string value in a plain dict, which is easy to
assert on -- the actual LLM non-determinism is confined to Triage's
*severity* classification, not to whether the graph advances to the right
node for a given severity, which is exactly the separation of concerns
`agents/supervisor.py`'s module docstring calls out as the reason routing
logic was kept out of prompts in the first place.
