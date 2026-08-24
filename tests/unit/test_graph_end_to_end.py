"""Full-graph integration test: triage -> diagnostic -> remediation ->
communications, exercised through the real compiled LangGraph state machine
with every dependency faked out (no network, no live LLM, no Postgres).

The FakeLLMClient responder keys are picked to match a substring that
appears uniquely in exactly one node's *user* prompt (never the system
prompt, since FakeLLMClient only inspects `prompt`):

- triage's prompt is the only one with a top-level "severity_hint" key.
- diagnostic's prompt is the only one with a top-level "log_error_count" key.
- remediation's prompt is the only one with a top-level "hypotheses" key.

communications never asks for structured JSON, so its two LLM calls (the
Slack draft and, for sev1/sev2, the postmortem draft) both fall through to
`default_response`.
"""

from __future__ import annotations

import json

from sre_agent.agents.deps import build_fake_deps
from sre_agent.core.llm_client import FakeLLMClient
from sre_agent.core.models import ActionType
from sre_agent.graph.build_graph import build_incident_graph
from sre_agent.graph.checkpointer import in_memory_checkpointer, thread_config
from sre_agent.graph.state import new_incident_state
from tests.factories import make_alert, make_incident

_TRIAGE_RESPONSE = json.dumps({"severity": "sev2", "rationale": "elevated 5xx rate on checkout"})
_DIAGNOSTIC_RESPONSE = json.dumps(
    {
        "hypotheses": [
            {
                "hypothesis": "Database connection pool exhausted after a recent deploy",
                "confidence": 0.82,
                "supporting_evidence": ["error logs show pool exhaustion"],
                "retrieved_runbook_ids": [],
            }
        ]
    }
)
_REMEDIATION_RESPONSE = json.dumps(
    {
        "actions": [
            {
                "action_type": "restart_pod",
                "target_resource": "checkout-7d9f-abc12",
                "parameters": {},
                "rationale": "restart to clear the crash loop",
            }
        ]
    }
)
_DEFAULT_COMMS_RESPONSE = (
    "Investigating checkout crash loop; restart executed; monitoring recovery."
)


def _responder() -> dict[str, str]:
    return {
        '"severity_hint"': _TRIAGE_RESPONSE,
        '"log_error_count"': _DIAGNOSTIC_RESPONSE,
        '"hypotheses"': _REMEDIATION_RESPONSE,
    }


def test_end_to_end_graph_run_completes_and_drafts_comms():
    llm = FakeLLMClient(responder=_responder(), default_response=_DEFAULT_COMMS_RESPONSE)
    deps = build_fake_deps(llm=llm)

    alert = make_alert(alert_name="PodCrashLoopBackOff", fingerprint="fp-e2e-0001")
    incident = make_incident(alert)
    state = new_incident_state(alert, incident, token_budget=deps.settings.incident_token_budget)

    graph = build_incident_graph(deps, in_memory_checkpointer())
    result = graph.invoke(state, config=thread_config(incident.incident_id))

    assert result["comms_draft"] == _DEFAULT_COMMS_RESPONSE
    assert result["incident"].severity.value == "sev2"

    actors = {event.actor for event in result["timeline"]}
    assert {
        "triage_agent",
        "diagnostic_agent",
        "remediation_agent",
        "communications_agent",
    } <= actors

    assert len(result["rca_hypotheses"]) == 1
    assert len(result["executed_actions"]) == 1
    assert result["executed_actions"][0].action_type == ActionType.RESTART_POD

    # sev2 incidents also get a postmortem draft.
    assert result["postmortem_draft"] == _DEFAULT_COMMS_RESPONSE

    # The Slack fake should have received exactly one incident-channel post.
    assert len(deps.slack.sent) == 1


def test_end_to_end_suppressed_alert_skips_diagnostic_and_remediation():
    llm = FakeLLMClient(responder=_responder(), default_response=_DEFAULT_COMMS_RESPONSE)
    deps = build_fake_deps(llm=llm)

    alert = make_alert(alert_name="Watchdog", fingerprint="fp-e2e-suppressed")
    incident = make_incident(alert)
    state = new_incident_state(alert, incident, token_budget=deps.settings.incident_token_budget)

    graph = build_incident_graph(deps, in_memory_checkpointer())
    result = graph.invoke(state, config=thread_config(incident.incident_id))

    actors = {event.actor for event in result["timeline"]}
    assert "diagnostic_agent" not in actors
    assert "remediation_agent" not in actors
    assert result["comms_draft"] == _DEFAULT_COMMS_RESPONSE
    assert result["rca_hypotheses"] == []
    assert result["executed_actions"] == []
    # sev4/suppressed incidents don't get a postmortem draft.
    assert result["postmortem_draft"] is None


def test_end_to_end_high_risk_action_ends_in_approval_queue_not_executed():
    responder = _responder()
    responder['"hypotheses"'] = json.dumps(
        {
            "actions": [
                {
                    "action_type": "rollback_deployment",
                    "target_resource": "checkout",
                    "parameters": {},
                    "rationale": "recent bad deploy",
                }
            ]
        }
    )
    llm = FakeLLMClient(responder=responder, default_response=_DEFAULT_COMMS_RESPONSE)
    deps = build_fake_deps(llm=llm)

    alert = make_alert(alert_name="PodCrashLoopBackOff", fingerprint="fp-e2e-rollback")
    incident = make_incident(alert)
    state = new_incident_state(alert, incident, token_budget=deps.settings.incident_token_budget)

    graph = build_incident_graph(deps, in_memory_checkpointer())
    result = graph.invoke(state, config=thread_config(incident.incident_id))

    assert result["executed_actions"] == []
    assert len(deps.approval_queue.list_pending()) == 1
    # comms_draft is whatever the (fake) LLM returned for the comms prompt --
    # FakeLLMClient only pattern-matches on the prompt text, not on our
    # scripted responder keys, so it falls through to default_response here.
    assert result["comms_draft"] == _DEFAULT_COMMS_RESPONSE
