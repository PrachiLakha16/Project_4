"""Golden-dataset regression harness: runs every fixture in
`evaluation/fixtures/` through the *real* compiled LangGraph incident graph
and scores triage/RCA/action-selection/approval-routing accuracy.

Run it directly:

    python -m evaluation.run_eval
    python -m evaluation.run_eval --threshold 0.9

Exits non-zero if the aggregate pass rate drops below the threshold -- this
is what a CI "eval gate" job checks (see tests/unit/test_evaluation_gate.py
for the pytest-native version of the same gate).

IMPORTANT DESIGN TRADEOFF -- read before extending this harness
-----------------------------------------------------------------
This harness must run deterministically, offline, and without an API key in
CI. To do that, `_responder_for_scenario()` below scripts a `FakeLLMClient`
per scenario using each fixture's OWN `expected_*` fields, so the "model"
the harness runs against literally already knows the right answer for
triage severity, RCA hypotheses, and the proposed remediation action.

That means a scenario failing here almost never indicates "the LLM got it
wrong" -- it indicates a bug in the deterministic graph/guardrail logic
downstream of the LLM call (routing, risk classification, approval gating),
since the LLM's answer was scripted to be correct. That is still a
genuinely useful regression signal for this codebase's control-flow logic,
and it is what keeps this harness fast and hermetic. It is deliberately NOT
a signal about real model quality.

To get a real regression signal against live model behavior, swap the
`FakeLLMClient` built in `_responder_for_scenario()` for a real
`sre_agent.core.llm_client.LLMClient` (requires ANTHROPIC_API_KEY/
OPENAI_API_KEY) and drop the scripted responder -- everything else in this
file (scoring, reporting, the CI gate) is unchanged either way.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from evaluation.scenarios.loader import EvalScenario, load_scenarios
from evaluation.scorer import (
    AggregateScore,
    PredictedOutcome,
    ScenarioOutcome,
    aggregate_scores,
    score_scenario,
)
from sre_agent.agents.deps import AgentDeps, build_fake_deps
from sre_agent.core.llm_client import FakeLLMClient
from sre_agent.core.models import ApprovalStatus, IncidentRecord, Severity
from sre_agent.graph.build_graph import build_incident_graph
from sre_agent.graph.checkpointer import in_memory_checkpointer, thread_config
from sre_agent.graph.state import new_incident_state

DEFAULT_THRESHOLD = 0.8

# Substrings that appear uniquely in exactly one agent node's *user* prompt
# (see agents/triage.py, agents/diagnostic.py, agents/remediation.py). See
# tests/unit/test_graph_end_to_end.py for the same technique with more
# detail on why each key is safe/unique.
_TRIAGE_KEY = '"severity_hint"'
_DIAGNOSTIC_KEY = '"log_error_count"'
_REMEDIATION_KEY = '"hypotheses"'
_DEFAULT_COMMS_RESPONSE = "Incident status update: agent is investigating and acting per runbook."


def _responder_for_scenario(scenario: EvalScenario) -> dict[str, str]:
    responder: dict[str, str] = {}

    triage_severity = scenario.expected_severity or "sev3"
    responder[_TRIAGE_KEY] = json.dumps(
        {"severity": triage_severity, "rationale": f"scripted for {scenario.scenario_id}"}
    )

    if scenario.expect_diagnostic_run:
        responder[_DIAGNOSTIC_KEY] = json.dumps(
            {
                "hypotheses": [
                    {
                        "hypothesis": f"scripted RCA hypothesis for {scenario.scenario_id}",
                        "confidence": 0.75,
                        "supporting_evidence": ["scripted evidence"],
                        "retrieved_runbook_ids": [],
                    }
                ]
            }
        )

    if scenario.expected_action_type:
        responder[_REMEDIATION_KEY] = json.dumps(
            {
                "actions": [
                    {
                        "action_type": scenario.expected_action_type,
                        "target_resource": scenario.alert.service,
                        "parameters": scenario.action_parameters,
                        "rationale": f"scripted remediation for {scenario.scenario_id}",
                    }
                ]
            }
        )
    else:
        responder[_REMEDIATION_KEY] = json.dumps({"actions": []})

    return responder


def _build_deps(scenario: EvalScenario) -> AgentDeps:
    llm = FakeLLMClient(
        responder=_responder_for_scenario(scenario), default_response=_DEFAULT_COMMS_RESPONSE
    )
    deps = build_fake_deps(llm=llm)
    if scenario.simulate_duplicate:
        deps.seen_fingerprints[scenario.alert.fingerprint] = "INC-EVAL-PRIOR-EXISTING"
    return deps


def run_scenario(scenario: EvalScenario) -> ScenarioOutcome:
    deps = _build_deps(scenario)
    alert = scenario.alert
    incident = IncidentRecord(
        fingerprint=alert.fingerprint,
        severity=Severity.SEV3,  # placeholder; triage overwrites it on most paths
        service=alert.service,
        namespace=alert.namespace,
        cluster=alert.cluster,
    )
    state = new_incident_state(alert, incident, token_budget=deps.settings.incident_token_budget)

    graph = build_incident_graph(deps, in_memory_checkpointer())
    result = graph.invoke(state, config=thread_config(incident.incident_id))

    proposed = result.get("proposed_actions") or []
    top_action = proposed[0] if proposed else None
    predicted = PredictedOutcome(
        severity=result["incident"].severity.value if result.get("incident") else None,
        action_type=top_action.action_type.value if top_action else None,
        requires_approval=bool(top_action) and top_action.approval_status == ApprovalStatus.PENDING,
        rca_ran=bool(result.get("rca_hypotheses")),
    )
    return score_scenario(scenario, predicted)


@dataclass
class EvalRun:
    outcomes: list[ScenarioOutcome]
    aggregate: AggregateScore


def run_all(fixtures_dir: Path | None = None) -> EvalRun:
    scenarios = load_scenarios(fixtures_dir)
    outcomes = [run_scenario(s) for s in scenarios]
    return EvalRun(outcomes=outcomes, aggregate=aggregate_scores(outcomes))


def _fmt_bool(value: bool | None) -> str:
    if value is None:
        return "skip"
    return "PASS" if value else "FAIL"


def print_report(run: EvalRun) -> None:
    header = f"{'scenario':<45} {'triage':<6} {'rca':<6} {'action':<6} {'approval':<9} {'overall'}"
    print(header)
    print("-" * len(header))
    for outcome in run.outcomes:
        print(
            f"{outcome.scenario_id:<45} "
            f"{_fmt_bool(outcome.triage_match):<6} "
            f"{_fmt_bool(outcome.rca_match):<6} "
            f"{_fmt_bool(outcome.action_match):<6} "
            f"{_fmt_bool(outcome.approval_match):<9} "
            f"{'PASS' if outcome.passed else 'FAIL'}"
        )
    print("-" * len(header))
    agg = run.aggregate
    print(f"scenarios: {agg.total_scenarios}")
    print(f"triage accuracy:   {agg.triage_accuracy:.1%}")
    print(f"rca accuracy:      {agg.rca_accuracy:.1%}")
    print(f"action accuracy:   {agg.action_accuracy:.1%}")
    print(f"approval accuracy: {agg.approval_accuracy:.1%}")
    print(f"overall pass rate: {agg.overall_pass_rate:.1%}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the golden-dataset regression harness")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="minimum overall pass rate required (default: 0.8)",
    )
    parser.add_argument(
        "--fixtures-dir", type=Path, default=None, help="override the fixtures directory"
    )
    args = parser.parse_args()

    run = run_all(args.fixtures_dir)
    print_report(run)

    if run.aggregate.overall_pass_rate < args.threshold:
        print(
            f"\nFAILED: overall pass rate {run.aggregate.overall_pass_rate:.1%} is below threshold {args.threshold:.1%}"
        )
        return 1

    print(
        f"\nOK: overall pass rate {run.aggregate.overall_pass_rate:.1%} meets threshold {args.threshold:.1%}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
