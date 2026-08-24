"""OpenTelemetry setup: one span per LangGraph node and per tool call.

`traced_node` wraps a graph node function so every supervisor/specialist
transition and every external tool call is visible as a span in the same
trace, keyed by incident_id, exported over OTLP to the collector configured
via OTEL_EXPORTER_OTLP_ENDPOINT.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from sre_agent.core.config import get_settings
from sre_agent.observability.metrics import AGENT_NODE_LATENCY

F = TypeVar("F", bound=Callable[..., Any])

_initialized = False


def configure_tracing() -> None:
    global _initialized
    if _initialized:
        return
    settings = get_settings()
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: settings.otel_service_name})
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    except Exception:
        # OTLP collector not reachable (e.g. local dev/tests) -- keep a no-op
        # provider so instrumentation code paths still execute.
        trace.set_tracer_provider(TracerProvider())
    _initialized = True


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("sre_agent")


def traced_node(node_name: str) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            started = time.monotonic()
            with tracer.start_as_current_span(f"graph.node.{node_name}") as span:
                span.set_attribute("sre_agent.node", node_name)
                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("sre_agent.status", "ok")
                    return result
                except Exception as exc:
                    span.set_attribute("sre_agent.status", "error")
                    span.record_exception(exc)
                    raise
                finally:
                    AGENT_NODE_LATENCY.labels(node=node_name).observe(time.monotonic() - started)

        return wrapper  # type: ignore[return-value]

    return decorator


def traced_tool_call(tool_name: str) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(f"tool.{tool_name}") as span:
                span.set_attribute("sre_agent.tool", tool_name)
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    span.set_attribute("sre_agent.status", "error")
                    span.record_exception(exc)
                    raise

        return wrapper  # type: ignore[return-value]

    return decorator
