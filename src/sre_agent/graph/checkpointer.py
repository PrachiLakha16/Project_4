"""Durable LangGraph checkpointer backed by Postgres.

Every graph.invoke()/graph.stream() call is scoped to a `thread_id` equal to
the incident_id, so the full node-by-node state history for an incident
survives process restarts and is resumable (e.g. after an approval decision
arrives asynchronously via the API, minutes or hours later). See
docs/adr/0002-postgres-checkpointer-and-pgvector.md for why Postgres over
Redis.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver

from sre_agent.core.config import Settings, get_settings


@contextmanager
def postgres_checkpointer(settings: Settings | None = None) -> Iterator[BaseCheckpointSaver]:
    """Context-managed Postgres checkpoint saver; call `.setup()` once per
    environment (idempotent) to create the checkpoint tables."""
    from langgraph.checkpoint.postgres import PostgresSaver

    settings = settings or get_settings()
    with PostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
        saver.setup()
        yield saver


def in_memory_checkpointer() -> BaseCheckpointSaver:
    """Used by tests and local/offline runs -- same BaseCheckpointSaver
    interface, no Postgres required."""
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


def thread_config(incident_id: str) -> dict:
    return {"configurable": {"thread_id": incident_id}}
