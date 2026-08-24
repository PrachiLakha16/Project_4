variable "environment" {
  description = "Deployment environment name, used in resource naming."
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the EKS cluster's IAM OIDC provider (from modules/eks output oidc_provider_arn)."
  type        = string
}

variable "oidc_provider_url" {
  description = "Issuer URL of the EKS cluster's OIDC provider, without the https:// scheme (from modules/eks output oidc_provider_url)."
  type        = string
}

variable "k8s_namespace" {
  description = "Kubernetes namespace the sre-incident-agent ServiceAccount lives in."
  type        = string
  default     = "sre-incident-agent"
}

variable "k8s_service_account_name" {
  description = "Kubernetes ServiceAccount name the IAM role trusts (must exactly match infrastructure/helm/sre-incident-agent values.yaml serviceAccount.name)."
  type        = string
  default     = "sre-incident-agent"
}

variable "secrets_manager_prefix" {
  description = "Prefix under which all this workload's Secrets Manager entries live, e.g. sre-incident-agent/prod. The IAM policy is scoped to arn:aws:secretsmanager:*:*:secret:<prefix>/*"
  type        = string
}

variable "aws_region" {
  description = "AWS region, used to scope the Secrets Manager resource ARN precisely rather than with a region wildcard where possible."
  type        = string
}

variable "tags" {
  description = "Resource tags."
  type        = map(string)
  default     = {}
}
