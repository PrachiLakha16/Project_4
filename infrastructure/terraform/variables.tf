# Root-level variables for the sre-incident-agent infrastructure stack.
#
# This stack intentionally does NOT stand up a new EKS control plane or VPC.
# It assumes both already exist (managed by a separate platform/landing-zone
# stack) and only provisions the resources this workload owns: RDS, IAM
# (IRSA), and Secrets Manager entries, wired to the existing cluster via data
# sources in modules/eks.

variable "environment" {
  description = "Deployment environment name (e.g. dev, staging, prod). Used to namespace resource names and the Secrets Manager prefix."
  type        = string
  default     = "prod"
}

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "Name of the existing EKS cluster this workload is deployed onto."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID of the existing VPC that hosts the EKS cluster and RDS instance."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs (in the existing VPC) used for the RDS subnet group. Should span at least 2 AZs."
  type        = list(string)
}

variable "eks_node_security_group_id" {
  description = "Security group ID attached to the EKS worker nodes / pods (e.g. the cluster's shared node SG, or an SG used by the pod's ENI under security groups for pods). Used to scope RDS ingress to only traffic originating from the cluster."
  type        = string
}

variable "k8s_namespace" {
  description = "Kubernetes namespace the sre-incident-agent workload is deployed into."
  type        = string
  default     = "sre-incident-agent"
}

variable "k8s_service_account_name" {
  description = "Kubernetes ServiceAccount name used by the sre-incident-agent Deployment (must match infrastructure/helm/sre-incident-agent values.yaml serviceAccount.name)."
  type        = string
  default     = "sre-incident-agent"
}

variable "db_instance_class" {
  description = "RDS instance class for the Postgres (checkpointer + pgvector) database."
  type        = string
  default     = "db.t3.medium"
}

variable "db_engine_version" {
  description = "Postgres engine version. Must support the pgvector extension (15.x / 16.x)."
  type        = string
  default     = "16.4"
}

variable "db_allocated_storage_gb" {
  description = "Allocated storage (GB) for the RDS instance."
  type        = number
  default     = 50
}

variable "db_multi_az" {
  description = "Whether to deploy the RDS instance in Multi-AZ for HA. Recommended true in prod."
  type        = bool
  default     = true
}

variable "db_name" {
  description = "Initial database name created on the RDS instance."
  type        = string
  default     = "sre_incident_agent"
}

variable "db_master_username" {
  description = "Master username for the RDS Postgres instance."
  type        = string
  default     = "sre_agent_admin"
}

variable "secrets_manager_prefix" {
  description = "Prefix under which all Secrets Manager entries for this workload are created. Must match AWS_SECRETS_MANAGER_PREFIX in .env.example / Helm values."
  type        = string
  default     = "sre-incident-agent/prod"
}

variable "tags" {
  description = "Common resource tags applied to everything this stack creates."
  type        = map(string)
  default = {
    Project   = "sre-incident-agent"
    ManagedBy = "terraform"
  }
}

# --- Application secret values (sensitive; supply via TF_VAR_* env vars or a
# secrets-backed .auto.tfvars that is never committed, not the checked-in
# terraform.tfvars.example) ---

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL used by the Communications Agent. Sourced from a secrets manager / CI secret store at apply time, never committed."
  type        = string
  sensitive   = true
  default     = ""
}

variable "slack_bot_token" {
  description = "Slack bot token (xoxb-...) used for richer Slack API calls beyond the incoming webhook."
  type        = string
  sensitive   = true
  default     = ""
}

variable "pagerduty_api_token" {
  description = "PagerDuty REST API token."
  type        = string
  sensitive   = true
  default     = ""
}

variable "pagerduty_routing_key" {
  description = "PagerDuty Events API v2 routing key."
  type        = string
  sensitive   = true
  default     = ""
}

variable "anthropic_api_key" {
  description = "Anthropic API key used by the LLM client."
  type        = string
  sensitive   = true
  default     = ""
}

variable "openai_api_key" {
  description = "OpenAI API key used by the LLM client when LLM_PROVIDER=openai."
  type        = string
  sensitive   = true
  default     = ""
}

variable "api_jwt_secret" {
  description = "Fallback HS256 shared secret for local/dev-parity environments. Production should prefer OIDC-issued RS256 JWTs verified against the org IdP's JWKS (see SECURITY.md); this value still needs to exist as a Secrets Manager entry for environments that use the HS256 path."
  type        = string
  sensitive   = true
  default     = ""
}
