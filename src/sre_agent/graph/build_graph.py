"""Assembles the supervisor + 4-specialist LangGraph state machine.

    triage --(sev1-3)--> diagnostic --> remediation --> communications --> END
       \\--(sev4/suppressed/duplicate/injection-flagged)--> communications --> END

Each specialist node writes `next_agent`; `agents.supervisor.route` reads it
to pick the outgoing edge. See ARCHITECTURE.md for the full diagram and
state-machine description.
"""

from __future__ import annotations

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from sre_agent.agents.communications import communications_node
from sre_agent.agents.deps import AgentDeps
from sre_agent.agents.diagnostic import diagnostic_node
from sre_agent.agents.remediation import remediation_node
from sre_agent.agents.supervisor import COMMUNICATIONS, DIAGNOSTIC, DONE, REMEDIATION, TRIAGE, route
from sre_agent.agents.triage import triage_node
from sre_agent.core.models import IncidentState


def build_incident_graph(deps: AgentDeps, checkpointer: BaseCheckpointSaver | None = None):
    graph = StateGraph(IncidentState)

    graph.add_node(TRIAGE, partial(triage_node, deps=deps))
    graph.add_node(DIAGNOSTIC, partial(diagnostic_node, deps=deps))
    graph.add_node(REMEDIATION, partial(remediation_node, deps=deps))
    graph.add_node(COMMUNICATIONS, partial(communications_node, deps=deps))

    graph.set_entry_point(TRIAGE)

    graph.add_conditional_edges(
        TRIAGE, route, {DIAGNOSTIC: DIAGNOSTIC, COMMUNICATIONS: COMMUNICATIONS}
    )
    graph.add_edge(DIAGNOSTIC, REMEDIATION)
    graph.add_edge(REMEDIATION, COMMUNICATIONS)
    graph.add_conditional_edges(COMMUNICATIONS, route, {DONE: END})

    return graph.compile(checkpointer=checkpointer)
