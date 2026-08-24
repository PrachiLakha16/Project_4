"""Tests for agents.supervisor: route() and assert_valid_transition()."""

from __future__ import annotations

import pytest

from sre_agent.agents.supervisor import (
    COMMUNICATIONS,
    DIAGNOSTIC,
    DONE,
    REMEDIATION,
    TRIAGE,
    assert_valid_transition,
    route,
)


def test_route_returns_next_agent_from_state():
    assert route({"next_agent": "diagnostic"}) == "diagnostic"
    assert route({"next_agent": "remediation"}) == "remediation"
    assert route({"next_agent": "communications"}) == "communications"
    assert route({"next_agent": "done"}) == "done"


def test_route_defaults_to_triage_when_next_agent_missing():
    assert route({}) == TRIAGE


def test_route_forces_communications_when_error_set_regardless_of_next_agent():
    state = {"next_agent": "remediation", "error": "tool call failed"}
    assert route(state) == COMMUNICATIONS


def test_route_no_error_key_behaves_like_no_error():
    state = {"next_agent": "diagnostic", "error": None}
    assert route(state) == DIAGNOSTIC


def test_valid_transition_triage_to_diagnostic():
    assert_valid_transition(TRIAGE, DIAGNOSTIC)  # should not raise


def test_valid_transition_triage_to_communications():
    assert_valid_transition(TRIAGE, COMMUNICATIONS)  # should not raise


def test_invalid_transition_triage_to_remediation_raises():
    with pytest.raises(ValueError):
        assert_valid_transition(TRIAGE, REMEDIATION)


def test_valid_transition_diagnostic_to_remediation():
    assert_valid_transition(DIAGNOSTIC, REMEDIATION)


def test_invalid_transition_diagnostic_to_communications_raises():
    with pytest.raises(ValueError):
        assert_valid_transition(DIAGNOSTIC, COMMUNICATIONS)


def test_valid_transition_remediation_to_communications():
    assert_valid_transition(REMEDIATION, COMMUNICATIONS)


def test_invalid_transition_remediation_to_diagnostic_raises():
    with pytest.raises(ValueError):
        assert_valid_transition(REMEDIATION, DIAGNOSTIC)


def test_any_node_to_done_is_always_valid():
    for node in (TRIAGE, DIAGNOSTIC, REMEDIATION, COMMUNICATIONS):
        assert_valid_transition(node, DONE)  # should not raise for any of them


def test_invalid_transition_from_unknown_node_raises():
    with pytest.raises(ValueError):
        assert_valid_transition("not_a_real_node", DIAGNOSTIC)
