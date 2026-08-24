"""Central runtime configuration, loaded from environment variables.

Secrets (API keys, DSNs, webhook URLs) are never hardcoded. In production they
are populated into the pod's environment by the External Secrets Operator
syncing from AWS Secrets Manager (see infrastructure/terraform/modules/secrets
and SECURITY.md). Locally, .env.example documents every required variable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_ALLOWLIST_PATH = str(
    Path(__file__).resolve().parent.parent / "guardrails" / "allowlist.yaml"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM routing
    llm_provider: str = Field(default="anthropic")
    anthropic_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")
    llm_model_fast: str = Field(default="claude-haiku-4-5")
    llm_model_strong: str = Field(default="claude-sonnet-5")
    incident_token_budget: int = Field(default=120_000)
    llm_timeout_seconds: float = Field(default=30.0)

    # Postgres (checkpointer + pgvector RAG store)
    postgres_dsn: str = Field(
        default="postgresql://sre_agent:changeme@localhost:5432/sre_incident_agent"
    )
    postgres_pool_min: int = Field(default=1)
    postgres_pool_max: int = Field(default=10)

    # Observability
    otel_exporter_otlp_endpoint: str = Field(default="http://localhost:4318")
    otel_service_name: str = Field(default="sre-incident-agent")
    prometheus_metrics_port: int = Field(default=9464)
    log_level: str = Field(default="INFO")

    # External systems
    prometheus_base_url: str = Field(default="")
    loki_base_url: str = Field(default="")
    kube_config_path: str = Field(default="")
    kube_context: str = Field(default="")
    slack_webhook_url: str = Field(default="")
    slack_bot_token: str = Field(default="")
    pagerduty_api_token: str = Field(default="")
    pagerduty_routing_key: str = Field(default="")

    # AWS / secrets
    aws_region: str = Field(default="us-east-1")
    aws_secrets_manager_prefix: str = Field(default="sre-incident-agent/prod")

    # API auth / RBAC
    api_jwt_secret: str = Field(default="dev-only-change-me")
    api_jwt_audience: str = Field(default="sre-incident-agent")

    # Guardrails
    approval_queue_stale_minutes: int = Field(default=15)
    allowlist_config_path: str = Field(default=_DEFAULT_ALLOWLIST_PATH)

    # Resilience defaults
    tool_call_timeout_seconds: float = Field(default=10.0)
    tool_call_max_retries: int = Field(default=3)
    circuit_breaker_failure_threshold: int = Field(default=5)
    circuit_breaker_reset_seconds: float = Field(default=30.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
