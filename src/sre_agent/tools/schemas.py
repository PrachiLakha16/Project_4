"""Typed input/output schemas for every tool callable by the LangGraph agents.

Kept separate from the client implementations so both the LangChain/LangGraph
tool wrappers (graph/) and the MCP server (mcp_server/) can share one
definition of "what a tool call looks like".
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PrometheusQueryInput(BaseModel):
    promql: str = Field(description="Instant or range PromQL query")
    start: str | None = Field(default=None, description="RFC3339 start for range queries")
    end: str | None = Field(default=None, description="RFC3339 end for range queries")
    step: str = Field(default="60s")


class PrometheusQueryOutput(BaseModel):
    query: str
    series: list[dict]
    summary: str


class LogQueryInput(BaseModel):
    namespace: str
    service: str
    since_minutes: int = Field(default=30, ge=1, le=1440)
    filter_text: str | None = Field(default=None, description="Loki LogQL line filter fragment")


class LogQueryOutput(BaseModel):
    service: str
    lines: list[str]
    error_count: int


class TraceQueryInput(BaseModel):
    service: str
    since_minutes: int = Field(default=30, ge=1, le=1440)
    min_duration_ms: int | None = None


class TraceQueryOutput(BaseModel):
    service: str
    slow_spans: list[dict]
    error_spans: list[dict]


class K8sGetPodsInput(BaseModel):
    namespace: str
    label_selector: str | None = None


class K8sGetPodsOutput(BaseModel):
    pods: list[dict]


class K8sRestartPodInput(BaseModel):
    namespace: str
    pod_name: str
    idempotency_key: str


class K8sRollbackDeploymentInput(BaseModel):
    namespace: str
    deployment: str
    idempotency_key: str


class K8sScaleDeploymentInput(BaseModel):
    namespace: str
    deployment: str
    replicas: int = Field(ge=0, le=100)
    idempotency_key: str


class K8sActionOutput(BaseModel):
    action: str
    resource: str
    status: str
    detail: str


class SlackPostInput(BaseModel):
    channel: str
    text: str
    thread_ts: str | None = None


class SlackPostOutput(BaseModel):
    ok: bool
    ts: str


class PagerDutyAckInput(BaseModel):
    incident_id: str
    note: str | None = None


class PagerDutyAckOutput(BaseModel):
    ok: bool
    status: str


class RunbookSearchInput(BaseModel):
    query: str
    top_k: int = Field(default=4, ge=1, le=10)
    service_filter: str | None = None


class RunbookSearchOutput(BaseModel):
    results: list[dict]
