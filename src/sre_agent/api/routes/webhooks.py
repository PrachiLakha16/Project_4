"""Inbound alert ingestion: Prometheus Alertmanager and PagerDuty webhooks.

Normalizes each provider's payload into `RawAlert` and kicks off (or
continues) the LangGraph incident graph, keyed by a stable fingerprint so
retried/duplicate webhook deliveries land on the same incident thread.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Request

from sre_agent.core.models import AlertSource, IncidentRecord, RawAlert, Severity
from sre_agent.graph.checkpointer import thread_config
from sre_agent.graph.state import new_incident_state
from sre_agent.observability.metrics import WEBHOOK_REQUESTS

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def _from_alertmanager(payload: dict[str, Any]) -> list[RawAlert]:
    alerts = []
    for a in payload.get("alerts", []):
        labels = a.get("labels", {})
        annotations = a.get("annotations", {})
        fingerprint = a.get("fingerprint") or _fingerprint(
            labels.get("alertname", ""), labels.get("namespace", ""), labels.get("service", "")
        )
        alerts.append(
            RawAlert(
                source=AlertSource.ALERTMANAGER,
                fingerprint=fingerprint,
                alert_name=labels.get("alertname", "Unknown"),
                severity_hint=labels.get("severity"),
                service=labels.get("service", labels.get("job", "unknown")),
                namespace=labels.get("namespace", "default"),
                cluster=labels.get("cluster", "default"),
                labels=labels,
                annotations=annotations,
                generator_url=a.get("generatorURL"),
                raw_payload=a,
            )
        )
    return alerts


def _from_pagerduty(payload: dict[str, Any]) -> list[RawAlert]:
    alerts = []
    for event in payload.get("messages", payload.get("events", [payload])):
        incident_data = event.get("incident", event)
        fingerprint = str(
            incident_data.get("incident_key")
            or incident_data.get("id")
            or _fingerprint(str(incident_data))
        )
        service = (
            incident_data.get("service", {}).get("summary", "unknown")
            if isinstance(incident_data.get("service"), dict)
            else str(incident_data.get("service", "unknown"))
        )
        alerts.append(
            RawAlert(
                source=AlertSource.PAGERDUTY,
                fingerprint=fingerprint,
                alert_name=incident_data.get("title", "PagerDuty Incident"),
                severity_hint=incident_data.get("urgency"),
                service=service,
                namespace=incident_data.get("custom_details", {}).get("namespace", "default")
                if isinstance(incident_data.get("custom_details"), dict)
                else "default",
                annotations={"summary": str(incident_data.get("title", ""))},
                labels={"urgency": str(incident_data.get("urgency", ""))},
                raw_payload=incident_data,
            )
        )
    return alerts


@router.post("/alertmanager")
async def alertmanager_webhook(request: Request) -> dict:
    payload = await request.json()
    alerts = _from_alertmanager(payload)
    incident_ids = [_ingest_alert(a) for a in alerts]
    WEBHOOK_REQUESTS.labels(source="alertmanager", status="accepted").inc()
    return {"accepted": len(alerts), "incident_ids": incident_ids}


@router.post("/pagerduty")
async def pagerduty_webhook(request: Request) -> dict:
    payload = await request.json()
    alerts = _from_pagerduty(payload)
    incident_ids = [_ingest_alert(a) for a in alerts]
    WEBHOOK_REQUESTS.labels(source="pagerduty", status="accepted").inc()
    return {"accepted": len(alerts), "incident_ids": incident_ids}


def _ingest_alert(alert: RawAlert) -> str:
    from sre_agent.api.app import get_app_state

    app_state = get_app_state()
    incident = IncidentRecord(
        fingerprint=alert.fingerprint,
        severity=Severity.SEV4,
        service=alert.service,
        namespace=alert.namespace,
        cluster=alert.cluster,
    )
    initial_state = new_incident_state(
        alert, incident, app_state.deps.settings.incident_token_budget
    )
    config = thread_config(incident.incident_id)
    app_state.graph.invoke(initial_state, config=config)  # type: ignore[call-overload]
    return incident.incident_id
