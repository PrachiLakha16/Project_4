output "secret_arns" {
  description = "Map of logical secret name -> full Secrets Manager ARN, for every secret this module creates."
  value       = { for k, v in aws_secretsmanager_secret.this : k => v.arn }
}

output "secret_names" {
  description = "Map of logical secret name -> Secrets Manager secret name (path), for wiring into External Secrets Operator SecretStore/ExternalSecret resources."
  value       = { for k, v in aws_secretsmanager_secret.this : k => v.name }
}
