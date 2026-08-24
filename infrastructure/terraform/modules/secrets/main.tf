# AWS Secrets Manager entries for sre-incident-agent, named under
# <secrets_manager_prefix>/<logical-name> (e.g. sre-incident-agent/prod/postgres-dsn).
#
# All values come in through sensitive input variables — never literal
# secrets in this file. The root module sources them from TF_VAR_* env vars
# populated by CI from an upstream secret store (see
# terraform.tfvars.example for the non-secret half of the picture).
#
# These entries are read back into the cluster by the IRSA role in
# modules/iam (secretsmanager:GetSecretValue/DescribeSecret only) via the
# External Secrets Operator or the AWS Secrets Manager CSI driver, and
# surfaced to the app as the env vars documented in .env.example.

locals {
  secrets = {
    postgres_dsn = {
      name        = "${var.secrets_manager_prefix}/postgres-dsn"
      description = "Postgres DSN (LangGraph checkpointer + pgvector runbook_chunks) -> POSTGRES_DSN"
      value       = var.postgres_dsn
    }
    slack_webhook_url = {
      name        = "${var.secrets_manager_prefix}/slack-webhook-url"
      description = "Slack incoming webhook URL -> SLACK_WEBHOOK_URL"
      value       = var.slack_webhook_url
    }
    slack_bot_token = {
      name        = "${var.secrets_manager_prefix}/slack-bot-token"
      description = "Slack bot token -> SLACK_BOT_TOKEN"
      value       = var.slack_bot_token
    }
    pagerduty_api_token = {
      name        = "${var.secrets_manager_prefix}/pagerduty-api-token"
      description = "PagerDuty REST API token -> PAGERDUTY_API_TOKEN"
      value       = var.pagerduty_api_token
    }
    pagerduty_routing_key = {
      name        = "${var.secrets_manager_prefix}/pagerduty-routing-key"
      description = "PagerDuty Events API v2 routing key -> PAGERDUTY_ROUTING_KEY"
      value       = var.pagerduty_routing_key
    }
    anthropic_api_key = {
      name        = "${var.secrets_manager_prefix}/anthropic-api-key"
      description = "Anthropic API key -> ANTHROPIC_API_KEY"
      value       = var.anthropic_api_key
    }
    openai_api_key = {
      name        = "${var.secrets_manager_prefix}/openai-api-key"
      description = "OpenAI API key -> OPENAI_API_KEY"
      value       = var.openai_api_key
    }
    api_jwt_secret = {
      name        = "${var.secrets_manager_prefix}/api-jwt-secret"
      description = "Dev-parity HS256 JWT shared secret -> API_JWT_SECRET"
      value       = var.api_jwt_secret
    }
  }
}

resource "aws_secretsmanager_secret" "this" {
  for_each = local.secrets

  name                    = each.value.name
  description             = each.value.description
  recovery_window_in_days = var.recovery_window_in_days

  tags = var.tags
}

resource "aws_secretsmanager_secret_version" "this" {
  for_each = local.secrets

  secret_id     = aws_secretsmanager_secret.this[each.key].id
  secret_string = each.value.value
}
