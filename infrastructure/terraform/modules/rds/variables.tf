variable "environment" {
  description = "Deployment environment name, used in resource naming."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID the RDS instance and its security group are created in."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the DB subnet group (span at least 2 AZs)."
  type        = list(string)
}

variable "eks_node_security_group_id" {
  description = "Security group ID of the EKS nodes/pods that are allowed to reach Postgres. Ingress is scoped to only this SG — no 0.0.0.0/0, no VPC-CIDR-wide access."
  type        = string
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t3.medium"
}

variable "engine_version" {
  description = "Postgres engine version. Must support the pgvector extension (15.x / 16.x)."
  type        = string
  default     = "16.4"
}

variable "allocated_storage_gb" {
  description = "Allocated storage in GB."
  type        = number
  default     = 50
}

variable "max_allocated_storage_gb" {
  description = "Upper bound for RDS storage autoscaling."
  type        = number
  default     = 200
}

variable "multi_az" {
  description = "Whether to deploy in Multi-AZ for HA. Recommended true in prod."
  type        = bool
  default     = true
}

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "sre_incident_agent"
}

variable "master_username" {
  description = "Master username for the RDS instance."
  type        = string
  default     = "sre_agent_admin"
}

variable "backup_retention_days" {
  description = "Automated backup retention period, in days."
  type        = number
  default     = 7
}

variable "deletion_protection" {
  description = "Whether to enable RDS deletion protection. Recommended true in prod."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Resource tags."
  type        = map(string)
  default     = {}
}
