"""Supervisor routing logic.

The supervisor itself is not an LLM call -- it is a deterministic router over
the `next_agent` field each specialist node writes into state. Centralizing
the edge-selection function here (rather than scattering conditional edges
across build_graph.py) keeps the handoff policy in one reviewable place and
is what the routing unit tests exercise directly.
"""

from __future__ import annotations

from sre_agent.core.models import IncidentState

TRIAGE = "triage"
DIAGNOSTIC = "diagnostic"
REMEDIATION = "remediation"
COMMUNICATIONS = "communications"
DONE = "done"

VALID_TRANSITIONS: dict[str, set[str]] = {
    TRIAGE: {DIAGNOSTIC, COMMUNICATIONS},
    DIAGNOSTIC: {REMEDIATION},
    REMEDIATION: {COMMUNICATIONS},
    COMMUNICATIONS: {DONE},
}


def route(state: IncidentState) -> str:
    """Returns the LangGraph destination node name (or END sentinel handled
    by build_graph.py) for the current state."""
    next_agent = state.get("next_agent", TRIAGE)
    if state.get("error"):
        return COMMUNICATIONS
    return next_agent


def assert_valid_transition(from_node: str, to_node: str) -> None:
    if to_node == DONE:
        return
    allowed = VALID_TRANSITIONS.get(from_node, set())
    if to_node not in allowed:
        raise ValueError(f"invalid handoff: {from_node} -> {to_node} (allowed: {sorted(allowed)})")
