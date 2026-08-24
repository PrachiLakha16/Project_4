"""Prometheus metrics exposed by the FastAPI service.

Scraped by the in-cluster Prometheus and rendered by the Grafana dashboard at
infrastructure/monitoring/grafana-dashboard.json.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

AGENT_NODE_LATENCY = Histogram(
    "sre_agent_node_latency_seconds",
    "Latency of a single LangGraph node execution",
    labelnames=("node",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

LLM_TOKENS_USED = Counter(
    "sre_agent_llm_tokens_total",
    "Total LLM tokens consumed",
    labelnames=("model", "direction"),
)

APPROVAL_QUEUE_DEPTH = Gauge(
    "sre_agent_approval_queue_depth",
    "Number of remediation actions currently pending human approval",
)

ACTION_RESULT = Counter(
    "sre_agent_action_result_total",
    "Remediation actions executed, labeled by outcome",
    labelnames=("action_type", "result"),
)

INCIDENTS_TRIAGED = Counter(
    "sre_agent_incidents_triaged_total",
    "Incidents that completed the triage node",
    labelnames=("severity", "deduplicated"),
)

TIME_TO_TRIAGE = Histogram(
    "sre_agent_time_to_triage_seconds",
    "Wall-clock time from alert ingestion to triage completion",
    buckets=(1, 2, 5, 10, 20, 30, 60, 120),
)

CIRCUIT_BREAKER_STATE = Gauge(
    "sre_agent_circuit_breaker_open",
    "1 if the circuit breaker for a dependency is open, else 0",
    labelnames=("dependency",),
)

WEBHOOK_REQUESTS = Counter(
    "sre_agent_webhook_requests_total",
    "Inbound alert webhook requests",
    labelnames=("source", "status"),
)
