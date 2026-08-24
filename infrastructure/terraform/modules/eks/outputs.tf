output "cluster_endpoint" {
  description = "API server endpoint of the existing EKS cluster."
  value       = data.aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64-encoded certificate authority data for the cluster."
  value       = data.aws_eks_cluster.this.certificate_authority[0].data
}

output "cluster_auth_token" {
  description = "Short-lived bearer token for authenticating to the cluster's API server."
  value       = data.aws_eks_cluster_auth.this.token
  sensitive   = true
}

output "oidc_provider_arn" {
  description = "ARN of the cluster's IAM OIDC provider, used to build the IRSA role's assume-role trust policy."
  value       = data.aws_iam_openid_connect_provider.this.arn
}

output "oidc_provider_url" {
  description = "Issuer URL of the cluster's OIDC provider, without the https:// scheme (as required in trust policy StringEquals conditions)."
  value       = local.oidc_issuer_host
}
