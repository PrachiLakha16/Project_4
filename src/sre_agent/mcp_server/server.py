"""MCP server exposing the read-only tool layer to other MCP clients.

Deliberately exposes a *subset* of the full tool surface -- Prometheus/Loki
queries, K8s pod listing, and runbook search -- and no mutating actions.
Remediation actions stay reachable only through the guarded LangGraph
Remediation Agent path (allowlist + risk classification + approval gate),
never directly via MCP, so an external MCP client (e.g. a chat client an
engineer is using ad hoc) can investigate an incident but cannot bypass
guardrails to mutate the cluster. See ARCHITECTURE.md "MCP exposure" section.

Run with:  python -m sre_agent.mcp_server.server
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from sre_agent.rag.retriever import RunbookRetriever
from sre_agent.tools.k8s_client import K8sClientProtocol
from sre_agent.tools.loki_client import LogClientProtocol
from sre_agent.tools.prometheus_client import PrometheusClientProtocol
from sre_agent.tools.schemas import (
    K8sGetPodsInput,
    LogQueryInput,
    PrometheusQueryInput,
    RunbookSearchInput,
)

mcp = MCPServer("sre-incident-agent-tools")


def register_tools(
    prometheus: PrometheusClientProtocol,
    logs: LogClientProtocol,
    k8s: K8sClientProtocol,
    retriever: RunbookRetriever,
) -> None:
    @mcp.tool()
    def query_prometheus(promql: str) -> dict:
        """Run an instant PromQL query against the cluster's Prometheus."""
        result = prometheus.instant_query(PrometheusQueryInput(promql=promql))
        return result.model_dump()

    @mcp.tool()
    def query_logs(
        namespace: str, service: str, since_minutes: int = 30, filter_text: str | None = None
    ) -> dict:
        """Query recent logs for a service in a namespace, optionally filtered."""
        result = logs.query_logs(
            LogQueryInput(
                namespace=namespace,
                service=service,
                since_minutes=since_minutes,
                filter_text=filter_text,
            )
        )
        return result.model_dump()

    @mcp.tool()
    def get_pods(namespace: str, label_selector: str | None = None) -> dict:
        """List pods in a namespace with phase and restart-count -- read-only."""
        result = k8s.get_pods(K8sGetPodsInput(namespace=namespace, label_selector=label_selector))
        return result.model_dump()

    @mcp.tool()
    def search_runbooks(query: str, top_k: int = 4, service_filter: str | None = None) -> dict:
        """Semantic search over the ingested runbook/postmortem corpus."""
        result = retriever.search(
            RunbookSearchInput(query=query, top_k=top_k, service_filter=service_filter)
        )
        return result.model_dump()


def main() -> None:
    from sre_agent.agents.deps import build_default_deps

    deps = build_default_deps()
    register_tools(deps.prometheus, deps.logs, deps.k8s, deps.retriever)
    mcp.run()


if __name__ == "__main__":
    main()
