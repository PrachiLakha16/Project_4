# Security

## Least-privilege IAM for the agent's K8s/AWS access

The agent runs as a dedicated Kubernetes `ServiceAccount`
(`infrastructure/helm/sre-incident-agent/templates/serviceaccount.yaml`)
annotated for IRSA, mapped to an IAM role
(`infrastructure/terraform/modules/iam`) that grants only:

- `secretsmanager:GetSecretValue` scoped to
  `arn:aws:secretsmanager:*:*:secret:sre-incident-agent/*` — nothing else.
- No `iam:*`, `eks:*` cluster-admin, or wildcard resource permissions. The
  role cannot create or modify IAM roles/policies, including its own — this
  is the specific control that stops the "widen my own permissions" prompt-
  injection scenario in `docs/incidents/002-prompt-injection-attempt.md` from
  having anywhere to go even if an injected instruction were followed.

Inside the cluster, the ServiceAccount is bound (via a namespaced `Role`, not
`ClusterRole`) to:

- `get/list/watch` on `pods` in the namespaces the agent is allowed to act in.
- `patch` on `deployments` and `deployments/scale` in those same namespaces.
- No `create`/`delete` on RBAC objects (`Role`, `ClusterRole`,
  `RoleBinding`), `ServiceAccount`, `Secret`, or any cluster-scoped resource.
- `NetworkPolicy` (`infrastructure/helm/.../networkpolicy.yaml`) restricts
  egress to Prometheus, Loki, the K8s API server, Postgres, and the Slack/
  PagerDuty API endpoints only.

## Secrets

Never hardcoded. `.env.example` documents every variable; real values are
injected as pod environment variables synced from AWS Secrets Manager by the
External Secrets Operator (or the Secrets Manager CSI driver), reading the
keys created in `infrastructure/terraform/modules/secrets`. Local development
uses a git-ignored `.env` file. CI uses repository/organization secrets, never
committed values — see `.github/workflows/ci.yml`.

## RBAC on the FastAPI endpoints

`src/sre_agent/api/rbac.py` verifies a bearer JWT on every mutating request.
`POST /approvals/{action_id}` requires the `sre-oncall` role claim
(`require_oncall` dependency) — an authenticated user without that role gets
a `403`. Read-only endpoints (`/healthz`, `/readyz`, `/metrics`,
`GET /approvals`) do not require the on-call role but still require a valid
token in production (the ingress layer additionally restricts `/metrics` to
the in-cluster Prometheus scrape target via NetworkPolicy).

In production the JWT is issued by the org's OIDC provider (RS256, verified
against the provider's JWKS) and carries group/role claims from
SSO — HS256 with `API_JWT_SECRET` is a local-dev-only shortcut, never used
outside `docker-compose`/`make run`.

## Input validation against prompt injection

Alert `annotations`, `labels`, and log lines are attacker-influenceable —
anyone who can trigger an alert or write an application log line can shape
this text. Before any of it reaches an LLM prompt,
`src/sre_agent/guardrails/prompt_injection.py`:

1. Scans for known injection patterns (`ignore previous instructions`,
   `you are now`, `grant yourself`, `widen ... permission`, embedded
   `<system>`/`<assistant>` tags, `curl`/`base64`/`exec(` fragments).
2. Wraps all untrusted text in an explicit `<untrusted_external_data>` block
   with an instruction to the model that it is data, never directives.
3. Caps field length so no single field can dominate the token budget or bury
   the real telemetry signal.

If any pattern is flagged, the Triage Agent escalates directly to the
Communications Agent (skipping Diagnostic/Remediation entirely) and marks
`metadata.prompt_injection_suspected = True`, so a human reviews the alert
before any tool call driven by its content occurs. This is defense in depth,
not the primary control — the primary control is that **remediation actions
are authorized by the allowlist + risk classifier + approval gate, not by
anything the LLM decides on its own**, so even a successful injection cannot
directly cause a mutating action; see `docs/THREAT_MODEL.md`.

## Reporting

This is a reference/portfolio project, not a monitored production service.
If you find an issue in the patterns demonstrated here, please open an issue
in this repository describing it.
