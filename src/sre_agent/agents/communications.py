"""Communications Agent: Slack incident updates and postmortem drafting.

Runs last on every path through the graph (including suppressed/duplicate
alerts, so on-call always gets a signal, and terminal incidents, so a
postmortem draft exists before the human review meeting).
"""

from __future__ import annotations

from sre_agent.agents.deps import AgentDeps
from sre_agent.core.llm_client import ModelTier
from sre_agent.core.models import IncidentState, TimelineEvent
from sre_agent.observability.tracing import traced_node
from sre_agent.tools.schemas import SlackPostInput

_COMMS_SYSTEM_PROMPT = (
    "You are the communications component of an SRE incident-response "
    "agent. Draft a concise Slack incident-channel update (4-6 lines, plain "
    "text, no markdown headers) summarizing current status, root cause "
    "hypothesis if known, and actions taken or pending approval."
)

_POSTMORTEM_SYSTEM_PROMPT = (
    "You are drafting a blameless postmortem for a resolved incident. "
    "Produce markdown with sections: Summary, Timeline, Root Cause, "
    "Remediation Actions Taken, Follow-ups. Be factual and concise, drawing "
    "only from the structured incident data provided."
)


@traced_node("communications")
def communications_node(state: IncidentState, deps: AgentDeps) -> dict:
    incident = state["incident"]
    alert = state["raw_alert"]
    timeline = state.get("timeline", [])
    hypotheses = state.get("rca_hypotheses", [])
    executed = state.get("executed_actions", [])
    proposed = state.get("proposed_actions", [])

    summary_lines = [
        f"[{incident.severity.value.upper() if incident.severity else 'n/a'}] {alert.alert_name} on {alert.service} ({incident.incident_id})",
        f"status={incident.status}"
        + (" duplicate" if incident.is_duplicate else "")
        + (" suppressed" if incident.suppressed_noise else ""),
    ]
    if hypotheses:
        summary_lines.append(
            f"top hypothesis: {hypotheses[0].hypothesis} (confidence {hypotheses[0].confidence:.2f})"
        )
    if executed:
        summary_lines.append(
            "auto-executed: "
            + ", ".join(f"{a.action_type.value} on {a.target_resource}" for a in executed)
        )
    pending = [a for a in proposed if a not in executed]
    if pending:
        summary_lines.append(
            "pending approval: "
            + ", ".join(f"{a.action_type.value} on {a.target_resource}" for a in pending)
        )

    response = deps.llm.complete(
        incident_id=incident.incident_id,
        tier=ModelTier.FAST,
        system=_COMMS_SYSTEM_PROMPT,
        prompt="\n".join(summary_lines),
        max_tokens=300,
    )
    slack_out = deps.slack.post_message(
        SlackPostInput(channel=f"#incident-{incident.incident_id.lower()}", text=response.text)
    )

    postmortem_draft = None
    if (
        incident.status not in ("suppressed", "duplicate")
        and incident.severity
        and incident.severity.value in ("sev1", "sev2")
    ):
        pm_prompt = "\n".join(
            [
                f"incident_id: {incident.incident_id}",
                f"service: {alert.service}",
                f"severity: {incident.severity.value}",
                "timeline:",
                *[
                    f"- {e.timestamp.isoformat()} [{e.actor}] {e.event_type}: {e.detail}"
                    for e in timeline
                ],
                "rca_hypotheses:",
                *[f"- {h.hypothesis} (confidence {h.confidence})" for h in hypotheses],
                "actions_taken:",
                *[
                    f"- {a.action_type.value} on {a.target_resource}: {a.execution_result}"
                    for a in executed
                ],
            ]
        )
        pm_response = deps.llm.complete(
            incident_id=incident.incident_id,
            tier=ModelTier.FAST,
            system=_POSTMORTEM_SYSTEM_PROMPT,
            prompt=pm_prompt,
            max_tokens=1000,
        )
        postmortem_draft = pm_response.text

    return {
        "comms_draft": response.text,
        "postmortem_draft": postmortem_draft,
        "timeline": [
            TimelineEvent(
                actor="communications_agent", event_type="slack_posted", detail=f"ts={slack_out.ts}"
            )
        ],
        "next_agent": "done",
    }
