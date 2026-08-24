# Root module: wires together the EKS (existing-cluster/IRSA data), RDS
# (Postgres checkpointer + pgvector), IAM (IRSA role for the agent's
# ServiceAccount), and Secrets Manager modules for sre-incident-agent.
#
# This stack is deliberately scoped to what this workload owns. The VPC,
# subnets, and EKS control plane are provisioned by a separate platform
# landing-zone stack and referenced here read-only via variables/data
# sources.

# --- Remote state backend (documented example) ---
#
# Uncomment and fill in a real bucket/table once the platform team's
# Terraform state backend exists. Left commented so this stack is runnable
# with local state out of the box in a sandbox/demo context.
#
# terraform {
#   backend "s3" {
#     bucket         = "REPLACE_ME-sre-incident-agent-tfstate"
#     key            = "sre-incident-agent/prod/terraform.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "REPLACE_ME-terraform-locks"
#     encrypt        = true
#   }
# }

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = var.tags
  }
}

# --- EKS: reference the existing cluster + its OIDC provider for IRSA ---

module "eks" {
  source = "./modules/eks"

  cluster_name = var.cluster_name
}

# --- RDS: Postgres for the LangGraph checkpointer + pgvector runbook_chunks
# table. Same instance for both per the repo ADR (Postgres over Redis for
# the checkpointer, pgvector over a standalone vector DB). ---

module "rds" {
  source = "./modules/rds"

  environment                = var.environment
  vpc_id                     = var.vpc_id
  subnet_ids                 = var.subnet_ids
  eks_node_security_group_id = var.eks_node_security_group_id
  instance_class              = var.db_instance_class
  engine_version               = var.db_engine_version
  allocated_storage_gb        = var.db_allocated_storage_gb
  multi_az                    = var.db_multi_az
  db_name                     = var.db_name
  master_username              = var.db_master_username
  tags                        = var.tags
}

# --- IAM: IRSA role for the agent's ServiceAccount, least-privilege per
# SECURITY.md (secretsmanager:GetSecretValue/DescribeSecret only, scoped to
# the sre-incident-agent/* secret prefix; no wildcard admin actions). ---

module "iam" {
  source = "./modules/iam"

  environment               = var.environment
  oidc_provider_arn         = module.eks.oidc_provider_arn
  oidc_provider_url         = module.eks.oidc_provider_url
  k8s_namespace              = var.k8s_namespace
  k8s_service_account_name   = var.k8s_service_account_name
  secrets_manager_prefix    = var.secrets_manager_prefix
  aws_region                = var.aws_region
  tags                      = var.tags
}

# --- Secrets Manager: application secrets, named under
# sre-incident-agent/prod/*, consumed by the IRSA role above and synced into
# the cluster via External Secrets Operator / the Secrets Manager CSI
# driver. ---

module "secrets" {
  source = "./modules/secrets"

  secrets_manager_prefix = var.secrets_manager_prefix

  # Postgres DSN is composed from the RDS module's outputs + the generated
  # master password, so the credential never has to be typed twice.
  postgres_dsn = "postgresql://${var.db_master_username}:${module.rds.master_password}@${module.rds.db_endpoint}/${var.db_name}"

  slack_webhook_url    = var.slack_webhook_url
  slack_bot_token      = var.slack_bot_token
  pagerduty_api_token  = var.pagerduty_api_token
  pagerduty_routing_key = var.pagerduty_routing_key
  anthropic_api_key    = var.anthropic_api_key
  openai_api_key       = var.openai_api_key
  api_jwt_secret       = var.api_jwt_secret

  tags = var.tags
}
