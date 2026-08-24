# Threat Model

STRIDE-based threat model for the sre-incident-agent system: an LLM-driven
agent that reads attacker-influenceable input (alert payloads, log content)
and, on some paths, takes mutating actions against production Kubernetes
infrastructure. The design principle running through every mitigation below
is stated in `SECURITY.md`: **the LLM is never the sole authority for a
mutating action** -- the allowlist, risk classifier, and approval gate are.

## Threat table

| Threat | STRIDE | Mitigation | Residual risk |
|---|---|---|---|
| Prompt injection via alert `annotations`/`labels`/log content, attempting to make the LLM emit unsafe instructions or reasoning | Tampering, Elevation of Privilege | `guardrails/prompt_injection.py`: pattern scan (`_SUSPICIOUS_PATTERNS`) flags known injection phrasing before any untrusted text reaches a prompt; flagged alerts route straight from Triage to Communications, skipping Diagnostic/Remediation entirely (`agents/triage.py`); all untrusted text is wrapped in `<untrusted_external_data>` blocks with explicit "this is data, not instructions" framing; field length capped at `MAX_FIELD_CHARS` (4000) to prevent budget/context abuse | Pattern scan is signature-based and can miss novel phrasing (see `docs/incidents/002-prompt-injection-attempt.md`'s follow-up on softer, socially-engineered framing). Primary control (allowlist + risk classifier + approval gate) bounds the damage even on a miss -- see next row. |
| Over-privileged tool access if the K8s/AWS IAM role were misconfigured, allowing an injected instruction (or a compromised model) to actually take a wide-blast-radius action | Elevation of Privilege | Least-privilege IRSA role: `secretsmanager:GetSecretValue` scoped to `sre-incident-agent/*` only, no `iam:*`/`eks:*`/wildcard permissions, cannot modify its own role (`SECURITY.md`). In-cluster: namespaced `Role` (not `ClusterRole`) with only `get/list/watch` pods and `patch` on `deployments`/`deployments/scale` in allowed namespaces; no RBAC-object, Secret, or cluster-scoped `create`/`delete`. `NetworkPolicy` restricts egress to Prometheus, Loki, K8s API, Postgres, Slack/PagerDuty only | If the Terraform/Helm templates were ever hand-edited to grant broader scope outside of code review, this control silently weakens -- mitigated by keeping IAM/RBAC definitions in version-controlled `infrastructure/` reviewed the same as application code, not console-clickable. |
| Approval-bypass: calling `POST /approvals/{action_id}` without the `sre-oncall` role, or treating a Slack reaction/reply as an authoritative approval | Elevation of Privilege, Spoofing | `api/rbac.py`'s `require_oncall` dependency verifies a bearer JWT and requires the `sre-oncall` role claim on every call to the approvals endpoint; unauthenticated or under-privileged callers get 401/403. Slack is explicitly documented as a notification surface only (`guardrails/approval_queue.py` module docstring, `SECURITY.md`) -- nothing in `ApprovalQueue.decide()` is reachable from a Slack event, only from the authenticated API route | JWT issuance and role-claim assignment live in the org's OIDC/SSO system, outside this repo's control -- a compromised SSO account with the `sre-oncall` claim can approve actions legitimately per this system's logic. Local-dev HS256 shared-secret mode (`API_JWT_SECRET`) is explicitly never used in production (`SECURITY.md`), but is worth calling out as a real footgun if that boundary were ever blurred. |
| Data exfiltration via RAG: a malicious or compromised runbook document used to leak incident data through retrieval into a context an attacker can read | Information Disclosure | Ingestion-time trust: `rag/ingest.py` only ingests `*.md` files from the `docs/runbooks/` path in this repository, which reach the corpus exclusively via the normal PR review process -- there is no live/user-submitted document ingestion path in the system as built. Retrieval-time scoping: `RunbookRetriever.search()` supports `service_filter`, and the Diagnostic Agent's query is built from the current incident's own alert fields, not attacker-controlled free text routed elsewhere | If a future feature allowed submitting runbooks outside PR review (e.g. an admin UI), this control would need to move to ingestion-time content scanning, not just path/review trust -- flagged as an explicit constraint on any such feature, not yet built. Retrieved chunk text is treated as untrusted before reaching a prompt (wrapped the same as alert data), which also limits what a poisoned chunk could achieve even if ingested. |
| Denial of service via alert flooding (flapping alert storms, duplicate fingerprint spam) | Denial of Service | Triage-stage dedup on `fingerprint` (`deps.seen_fingerprints`) short-circuits duplicate incidents straight to Communications without a full graph run; known-noisy alert names (`Watchdog`, `InfoInhibitor`) are suppressed outright before any LLM call; per-incident hard token-budget cutoff (`INCIDENT_TOKEN_BUDGET`, `LLMBudgetExceededError` in `core/llm_client.py`) bounds worst-case spend per incident thread even under a flood; `CircuitBreaker` (`tools/resilience.py`) trips per-dependency after repeated failures so a struggling Prometheus/Loki/K8s API doesn't compound the flood into a stuck-and-retrying graph | Dedup is fingerprint-based; a flood of alerts with *distinct* fingerprints (e.g. per-pod alerts during a real widescale outage) still drives one graph run and one Triage LLM call per incident, which is the correct behavior for genuine incidents but is also the same shape an attacker able to trigger many distinct alerts could exploit to run up LLM spend across many incidents rather than one. No global (cross-incident) rate limit currently exists at the webhook layer beyond `WEBHOOK_REQUESTS` observability. |
| Audit/repudiation: no reliable record of who approved a mutating action, or of what the agent did and why | Repudiation | `guardrails/audit.py`'s append-only `AuditEntry` log records every guardrail decision (`action_blocked`, `approval_requested`, `approval_decided`, `approval_expired`) with `actor`, `incident_id`, and structured `detail`; `PostgresAuditLog` persists to the same Postgres instance as the checkpointer/RAG store (ADR 0002) so entries survive restarts and are queryable for postmortems/compliance. `ApprovalQueue.decide()` always records `decided_by` from the authenticated principal, never a client-supplied field | `InMemoryAuditLog` (used in tests and, unless explicitly swapped, in unconfigured local dev) is not durable -- deployments must confirm `PostgresAuditLog` is actually wired in for any environment where audit trail matters. The audit log itself has no tamper-evidence (e.g. hash chaining) beyond database access control; a Postgres-level compromise could rewrite history undetected. |
| Spoofed/forged inbound alert webhook (Alertmanager/PagerDuty) triggering fabricated incidents or attempting to drive the graph with attacker-authored alert content | Spoofing, Tampering | Webhook authenticity is expected to be enforced at the ingress/API layer (shared secret or mTLS per the alerting platform's standard webhook-auth mechanism) ahead of `api/routes/webhooks.py`; regardless of source authenticity, all alert-derived text still passes through the same prompt-injection sanitization as any other alert, and no webhook-derived field is ever interpolated directly into a mutating tool call (actions are constructed from the constrained `ActionType` enum and target fields validated by `tools/schemas.py`, not raw webhook text) | This system's own code does not itself implement webhook signature verification -- it is a deployment-time responsibility documented as an assumption. An environment that skips it would rely entirely on the prompt-injection and guardrail layers, which are designed as defense-in-depth, not as the sole control for this specific threat. |

## Narrative: top risks in more depth

### 1. Prompt injection (Tampering / Elevation of Privilege)

This is the threat the system is most explicitly designed around, because
alert annotations and log lines are, by construction, text that anyone able
to trigger an alert or write an application log line can shape. The
mitigation is deliberately layered rather than relying on the pattern scan
alone: even a phrasing that evades every regex in `_SUSPICIOUS_PATTERNS`
still reaches the LLM only inside an `<untrusted_external_data>` block that
the system prompt tells the model to treat as data. And even if the model
were fully compromised by that content, the actions it could cause are
bounded by the `ActionType` enum and gated by the allowlist/risk/approval
chain -- "widen my IAM role" and "run this arbitrary shell command" are
simply not expressible actions in this system, independent of what the LLM
says. `docs/incidents/002-prompt-injection-attempt.md` is a real occurrence
of this threat and documents all three layers holding.

### 2. Over-privileged tool access (Elevation of Privilege)

The blast-radius reasoning here is symmetric with ADR 0003's allowlist
design: even a fully-trusted, uncompromised agent making its own best
judgment should not be able to touch `kube-system`, create IAM policies, or
scale a `platform-shared` tenant without a human in the loop, because the
cost of a mistake there is disproportionate to the value of full autonomy.
The IAM role and K8s RBAC are the last line of defense if every
application-layer guardrail were somehow bypassed -- which is exactly why
they're scoped as tightly as they are rather than trusting the allowlist
alone.

### 3. Approval-bypass (Elevation of Privilege / Spoofing)

The design choice that Slack is notification-only, and the API (with RBAC)
is the sole authorization boundary, exists specifically because Slack
messages, reactions, and threads are easy to spoof or misattribute compared
to a role-claim-bearing JWT verified server-side. An engineer without the
`sre-oncall` claim reacting with an emoji in the incident channel has zero
effect on `ApprovalQueue` state -- only `POST /approvals/{action_id}`,
authenticated and role-checked, does.

### 4. RAG-based data exfiltration (Information Disclosure)

Because the corpus is populated exclusively from `docs/runbooks/*.md` via
normal git/PR review (`rag/ingest.py`'s `ingest_directory()` reads a local
docs directory, not a network-submitted document), there is currently no
attacker-reachable path to get content into the RAG store at all -- the
threat is more "what if this changes" than "what is exploitable today."
The `service_filter` scoping on retrieval (`similarity_search(...,
service_filter=...)`) is a secondary control limiting which chunks a given
incident's query can surface, relevant mainly if the corpus grows to include
content with genuinely different sensitivity levels across services.

### 5. Denial of service via alert flooding

The layered defense here (fingerprint dedup, noisy-alert suppression,
per-incident token budget, circuit breakers) protects against both
accidental storms (a flapping alert, a misconfigured rule) and deliberate
ones. The main acknowledged gap is the absence of a cross-incident,
account-level rate limit at the webhook ingestion layer -- today, cost
containment is per-incident (`INCIDENT_TOKEN_BUDGET`), not aggregate. This
is flagged as an explicit residual risk above rather than treated as
resolved.
