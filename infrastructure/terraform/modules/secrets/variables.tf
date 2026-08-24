variable "secrets_manager_prefix" {
  description = "Prefix under which all secrets are created, e.g. sre-incident-agent/prod. Must match AWS_SECRETS_MANAGER_PREFIX and the IAM module's secrets_manager_prefix."
  type        = string
}

variable "postgres_dsn" {
  description = "Full Postgres connection string (POSTGRES_DSN), including the generated master password. Sourced from the rds module's outputs by the root module — never a literal value here."
  type        = string
  sensitive   = true
}

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL (SLACK_WEBHOOK_URL)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "slack_bot_token" {
  description = "Slack bot token (SLACK_BOT_TOKEN)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "pagerduty_api_token" {
  description = "PagerDuty REST API token (PAGERDUTY_API_TOKEN)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "pagerduty_routing_key" {
  description = "PagerDuty Events API v2 routing key (PAGERDUTY_ROUTING_KEY)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "anthropic_api_key" {
  description = "Anthropic API key (ANTHROPIC_API_KEY)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "openai_api_key" {
  description = "OpenAI API key (OPENAI_API_KEY)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "api_jwt_secret" {
  description = "HS256 shared secret for the dev-parity JWT path (API_JWT_SECRET). Production should prefer OIDC-issued RS256 JWTs; see SECURITY.md."
  type        = string
  sensitive   = true
  default     = ""
}

variable "recovery_window_in_days" {
  description = "Number of days AWS retains a deleted secret before permanent removal. 0 disables the recovery window (immediate delete) - useful for ephemeral/demo environments only."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Resource tags."
  type        = map(string)
  default     = {}
}
