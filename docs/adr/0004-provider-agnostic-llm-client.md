# 0004: Provider-agnostic `LLMClient` with tiered model routing instead of a hardcoded SDK

## Status

Accepted.

## Context

Four agent nodes call an LLM, each with different accuracy/latency/cost
requirements: Triage needs one cheap, fast call to classify severity within
the time-to-triage SLO; Diagnostic/RCA needs the strongest reasoning
available because RCA quality is, per `agents/diagnostic.py`'s own
docstring, "the highest-leverage part of this system"; Remediation planning
is fast/cheap (propose 1-2 actions from already-computed hypotheses);
Communications is fast/cheap (draft Slack text and postmortem markdown).

If agent code called `anthropic.Anthropic(...)` or `openai.OpenAI(...)`
directly, three problems follow: (1) every agent node hardcodes a vendor
choice that becomes expensive to change later, (2) there's no single place
to enforce the per-incident token budget cutoff, and (3) `FakeLLMClient`
substitution for deterministic testing (see `tests/`, `evaluation/`) would
need to happen per-SDK instead of behind one interface.

## Decision

`LLMClient` (`src/sre_agent/core/llm_client.py`) is the only way agent code
talks to an LLM. It exposes one method, `complete(incident_id, tier, system,
prompt, max_tokens, temperature)`, where `tier` is a `ModelTier` enum
(`FAST` or `STRONG`) rather than a model name. `_model_for(tier)` resolves
the tier to a concrete model from settings
(`LLM_MODEL_FAST=claude-haiku-4-5`, `LLM_MODEL_STRONG=claude-sonnet-5` in
`.env.example`), and `LLM_PROVIDER` (`anthropic` or `openai`) selects which
private `_call_anthropic`/`_call_openai` method actually makes the network
call. Agent modules import `ModelTier`, never `anthropic` or `openai`.

Model-tier assignment per agent, from `ARCHITECTURE.md`'s agent table and
the agent source:

| Agent | Tier | Why |
|---|---|---|
| Triage | `FAST` | One classification call, must stay well inside the time-to-triage SLO; JSON-only output, low reasoning depth needed. |
| Diagnostic/RCA | `STRONG` | Synthesizes correlated metrics, logs, and RAG excerpts into ranked hypotheses -- the actual "is this agent useful" moment. |
| Remediation (planning) | `FAST` | Structured extraction from already-computed hypotheses into 1-2 action proposals, not open-ended reasoning. |
| Communications | `FAST` | Templated summarization; RAG/RCA-quality reasoning isn't needed to draft a status update. |

`complete()` also owns the budget check: before every call it computes
`cost_ledger.remaining_budget(incident_id, INCIDENT_TOKEN_BUDGET)` and raises
`LLMBudgetExceededError` if it's exhausted, so budget enforcement lives once,
centrally, rather than being duplicated (or forgotten) per agent. Every real
call's usage is recorded into `CostLedger` regardless of provider, so cost
accounting is provider-agnostic too.

`FakeLLMClient` implements the identical `complete()` signature with
scripted, substring-matched responses and no network calls, and still routes
through `CostLedger` so budget-enforcement logic is exercised the same way
in tests as in production (see `docs/INTERVIEW_QA.md` Q9).

## Consequences

**Positive**

- Swapping the strong-tier model for a different provider/version (e.g.
  moving RCA to a different vendor because of a quality or cost change) is a
  one-file, one-line change (`LLM_MODEL_STRONG` / `LLM_PROVIDER` in config),
  not a multi-agent refactor.
- Per-environment or future per-tenant overrides are one settings change,
  not a code change scattered across four agent files.
- Token-budget and cost-ledger logic (`observability/cost.py`) is enforced
  exactly once, at the client boundary, so no agent can accidentally bypass
  it by calling a provider SDK directly.
- Deterministic testing: `FakeLLMClient`'s `responder` dict maps prompt
  substrings to canned JSON, so `tests/unit/*` and
  `evaluation/run_eval.py`'s golden-dataset scoring don't depend on live
  model output or API keys.

**Negative / tradeoffs**

- The abstraction is intentionally thin -- it does not normalize every
  provider-specific capability (e.g. Anthropic's extended thinking blocks,
  OpenAI's function-calling schema differences). If an agent ever needs a
  provider-specific feature, that pressure will want to leak through the
  `LLMClient` interface, and keeping it clean will take active discipline.
- `ModelTier` is a blunt two-level knob. A system with more heterogeneous
  workloads (e.g. wanting a "cheapest possible" tier for pure formatting
  tasks) would need a third tier and someone deciding where the line falls,
  which is a design conversation, not a config change.
- Cost/latency tuning is still per-tier, not per-agent -- if Remediation
  planning ever needed a different model than Communications despite both
  being `FAST` today, that requires either a new tier or an escape hatch in
  the client.
