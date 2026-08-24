"""Append-only audit log of every guardrail decision and executed action.

Real deployments write to a Postgres `audit_log` table (same instance as the
checkpointer) so entries survive pod restarts and are queryable for
postmortems/compliance; `InMemoryAuditLog` backs tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4


@dataclass
class AuditEntry:
    entry_id: str = field(default_factory=lambda: str(uuid4()))
    incident_id: str = ""
    actor: str = ""
    event_type: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class AuditLogProtocol(Protocol):
    def record(self, entry: AuditEntry) -> None: ...
    def for_incident(self, incident_id: str) -> list[AuditEntry]: ...


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self._entries.append(entry)

    def for_incident(self, incident_id: str) -> list[AuditEntry]:
        return [e for e in self._entries if e.incident_id == incident_id]

    def all(self) -> list[AuditEntry]:
        return list(self._entries)


class PostgresAuditLog:
    """DDL: audit_log(entry_id text pk, incident_id text, actor text,
    event_type text, detail jsonb, timestamp timestamptz)."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def ensure_schema(self) -> None:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    entry_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    detail JSONB NOT NULL DEFAULT '{}',
                    timestamp TIMESTAMPTZ NOT NULL
                )
                """
            )
            conn.commit()

    def record(self, entry: AuditEntry) -> None:
        import json

        import psycopg

        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO audit_log VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    entry.entry_id,
                    entry.incident_id,
                    entry.actor,
                    entry.event_type,
                    json.dumps(entry.detail),
                    entry.timestamp,
                ),
            )
            conn.commit()

    def for_incident(self, incident_id: str) -> list[AuditEntry]:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT entry_id, incident_id, actor, event_type, detail, timestamp FROM audit_log WHERE incident_id = %s ORDER BY timestamp",
                (incident_id,),
            ).fetchall()
        return [
            AuditEntry(
                entry_id=r[0],
                incident_id=r[1],
                actor=r[2],
                event_type=r[3],
                detail=r[4],
                timestamp=r[5],
            )
            for r in rows
        ]
