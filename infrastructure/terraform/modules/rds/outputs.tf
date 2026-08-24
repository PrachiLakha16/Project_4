output "db_endpoint" {
  description = "Postgres connection endpoint, host:port."
  value       = aws_db_instance.this.endpoint
}

output "db_address" {
  description = "Postgres host address, without port."
  value       = aws_db_instance.this.address
}

output "db_port" {
  description = "Postgres port."
  value       = aws_db_instance.this.port
}

output "master_password" {
  description = "Generated master password for the RDS instance. Consumed by the secrets module to build the full POSTGRES_DSN; never written to logs or the tfvars file."
  value       = random_password.master.result
  sensitive   = true
}

output "security_group_id" {
  description = "Security group ID attached to the RDS instance."
  value       = aws_security_group.rds.id
}
