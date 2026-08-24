"""Pytest-native wrapper around the golden-dataset eval harness.

This makes a plain `pytest` run (no extra invocation needed) catch an eval
regression, in addition to `python -m evaluation.run_eval` being runnable
standalone / from CI as an explicit gate step. Both paths share the exact
same `evaluation.run_eval.run_all()` logic -- this file adds no scoring
logic of its own, just an assertion and a printed report for readability
when running under `pytest -s`.
"""

from __future__ import annotations

from evaluation.run_eval import DEFAULT_THRESHOLD, print_report, run_all


def test_golden_dataset_meets_aggregate_threshold():
    run = run_all()
    print_report(run)

    assert run.aggregate.total_scenarios >= 12, "expected at least 12 golden-dataset scenarios"
    assert run.aggregate.overall_pass_rate >= DEFAULT_THRESHOLD, (
        f"golden-dataset overall pass rate {run.aggregate.overall_pass_rate:.1%} "
        f"fell below the {DEFAULT_THRESHOLD:.0%} CI gate threshold"
    )


def test_every_scenario_individually_documented_with_notes():
    """Sanity check on the fixtures themselves: every scenario should carry a
    human-readable rationale, since these fixtures double as living
    documentation of the guardrail policy."""
    run = run_all()
    for outcome in run.outcomes:
        assert outcome.scenario.description.strip(), (
            f"{outcome.scenario_id} is missing a description"
        )
        assert outcome.scenario.notes.strip(), f"{outcome.scenario_id} is missing notes"
