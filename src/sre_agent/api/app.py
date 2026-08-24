"""FastAPI application: webhook ingestion, approval gate, health/metrics.

Wires the LangGraph incident graph in on `startup` using a process-local
in-memory checkpointer by default; set `SRE_AGENT_USE_POSTGRES_CHECKPOINTER=1`
(or run via the Helm chart, which sets it) to use the durable Postgres
checkpointer instead.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from langgraph.graph.state import CompiledStateGraph

from sre_agent.agents.deps import AgentDeps, build_default_deps
from sre_agent.api.routes import approvals, health, webhooks
from sre_agent.core.config import get_settings
from sre_agent.graph.build_graph import build_incident_graph
from sre_agent.graph.checkpointer import in_memory_checkpointer
from sre_agent.observability.tracing import configure_tracing

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    deps: AgentDeps
    graph: CompiledStateGraph


_app_state: AppState | None = None


def get_app_state() -> AppState:
    if _app_state is None:
        raise RuntimeError("application not started: get_app_state() called before startup")
    return _app_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _app_state
    configure_tracing()
    deps = build_default_deps()

    if os.getenv("SRE_AGENT_USE_POSTGRES_CHECKPOINTER") == "1":
        from sre_agent.graph.checkpointer import postgres_checkpointer

        with postgres_checkpointer(deps.settings) as checkpointer:
            _app_state = AppState(deps=deps, graph=build_incident_graph(deps, checkpointer))
            yield
    else:
        checkpointer = in_memory_checkpointer()
        _app_state = AppState(deps=deps, graph=build_incident_graph(deps, checkpointer))
        yield

    _app_state = None


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(title="SRE Incident Agent", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(approvals.router)

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        logger.warning("OpenTelemetry FastAPI instrumentation unavailable; continuing without it")

    return app


app = create_app()
