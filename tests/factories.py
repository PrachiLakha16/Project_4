"""Plain (non-fixture) factory helpers shared by conftest.py and test modules.

Kept separate from conftest.py so test modules can `import tests.factories`
directly without relying on pytest's special-cased conftest collection.
"""

from __future__ import annotations

from sre_agent.core.models import AlertSource, IncidentRecord, RawAlert, Severity


def make_alert(**overrides) -> RawAlert:
    """Builds a RawAlert with sane defaults, overridable per test."""
    defaults = dict(
        source=AlertSource.ALERTMANAGER,
        fingerprint="fp-test-0001",
        alert_name="PodCrashLoopBackOff",
        severity_hint="warning",
        service="checkout",
        namespace="prod",
        cluster="prod-us-east-1",
        labels={"team": "checkout"},
        annotations={"summary": "checkout pod is crash-looping"},
    )
    defaults.update(overrides)
    return RawAlert(**defaults)


def make_incident(alert: RawAlert | None = None, **overrides) -> IncidentRecord:
    """Builds an IncidentRecord matching an alert, with a placeholder severity
    (real severity is assigned by the triage node)."""
    alert = alert or make_alert()
    defaults = dict(
        fingerprint=alert.fingerprint,
        severity=Severity.SEV3,
        service=alert.service,
        namespace=alert.namespace,
        cluster=alert.cluster,
    )
    defaults.update(overrides)
    return IncidentRecord(**defaults)
