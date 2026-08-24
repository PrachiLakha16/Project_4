output "rds_endpoint" {
  description = "Connection endpoint (host:port) for the Postgres instance backing the LangGraph checkpointer and pgvector runbook_chunks table."
  value       = module.rds.db_endpoint
}

output "rds_security_group_id" {
  description = "Security group ID attached to the RDS instance."
  value       = module.rds.security_group_id
}

output "iam_role_arn" {
  description = "ARN of the IRSA-mapped IAM role assumed by the sre-incident-agent ServiceAccount. Wire this into infrastructure/helm/sre-incident-agent values.yaml serviceAccount.annotations['eks.amazonaws.com/role-arn']."
  value       = module.iam.role_arn
}

output "secrets_manager_arns" {
  description = "ARNs of all Secrets Manager entries created for this workload, keyed by logical secret name."
  value       = module.secrets.secret_arns
}

output "oidc_provider_arn" {
  description = "ARN of the EKS cluster's IAM OIDC provider, used to build the IRSA trust policy."
  value       = module.eks.oidc_provider_arn
}
