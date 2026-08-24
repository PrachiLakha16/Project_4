"""Scoring functions for the golden-dataset regression harness.

Four dimensions are scored per scenario:

- triage accuracy: predicted severity == expected severity (skipped for
  scenarios where severity is not deterministically assigned, e.g. a
  suppressed/noisy alert -- `expected_severity` is null in the fixture).
- RCA-ran: did the Diagnostic Agent actually run and produce >=1 hypothesis
  exactly when the scenario expects it to reach that node.
- action selection: predicted top proposed action_type == expected.
- approval routing: predicted requires_approval == expected.
"""

from __future__ import annotations

from dataclasses import dataclass

from evaluation.scenarios.loader import EvalScenario


@dataclass
class PredictedOutcome:
    """What actually came out of running a scenario through the real graph."""

    severity: str | None
    action_type: str | None
    requires_approval: bool
    rca_ran: bool


@dataclass
class ScenarioOutcome:
    scenario_id: str
    triage_match: bool | None  # None => skipped (expected_severity was null)
    rca_match: bool
    action_match: bool
    approval_match: bool
    predicted: PredictedOutcome
    scenario: EvalScenario

    @property
    def passed(self) -> bool:
        """A scenario passes if every *applicable* dimension matched."""
        dims = [self.rca_match, self.action_match, self.approval_match]
        if self.triage_match is not None:
            dims.append(self.triage_match)
        return all(dims)


def score_scenario(scenario: EvalScenario, predicted: PredictedOutcome) -> ScenarioOutcome:
    triage_match: bool | None
    if scenario.expected_severity is None:
        triage_match = None
    else:
        triage_match = predicted.severity == scenario.expected_severity

    rca_match = predicted.rca_ran == scenario.expect_diagnostic_run
    action_match = predicted.action_type == scenario.expected_action_type
    approval_match = predicted.requires_approval == scenario.expected_requires_approval

    return ScenarioOutcome(
        scenario_id=scenario.scenario_id,
        triage_match=triage_match,
        rca_match=rca_match,
        action_match=action_match,
        approval_match=approval_match,
        predicted=predicted,
        scenario=scenario,
    )


@dataclass
class AggregateScore:
    total_scenarios: int
    triage_accuracy: float
    rca_accuracy: float
    action_accuracy: float
    approval_accuracy: float
    overall_pass_rate: float  # fraction of scenarios where every applicable dimension matched


def aggregate_scores(outcomes: list[ScenarioOutcome]) -> AggregateScore:
    if not outcomes:
        return AggregateScore(0, 0.0, 0.0, 0.0, 0.0, 0.0)

    triage_scored = [o.triage_match for o in outcomes if o.triage_match is not None]
    triage_accuracy = (sum(triage_scored) / len(triage_scored)) if triage_scored else 1.0

    rca_accuracy = sum(o.rca_match for o in outcomes) / len(outcomes)
    action_accuracy = sum(o.action_match for o in outcomes) / len(outcomes)
    approval_accuracy = sum(o.approval_match for o in outcomes) / len(outcomes)
    overall_pass_rate = sum(o.passed for o in outcomes) / len(outcomes)

    return AggregateScore(
        total_scenarios=len(outcomes),
        triage_accuracy=triage_accuracy,
        rca_accuracy=rca_accuracy,
        action_accuracy=action_accuracy,
        approval_accuracy=approval_accuracy,
        overall_pass_rate=overall_pass_rate,
    )
