output "role_arn" {
  description = "ARN of the IRSA-mapped IAM role. Set as the eks.amazonaws.com/role-arn annotation on the ServiceAccount in infrastructure/helm/sre-incident-agent."
  value       = aws_iam_role.irsa.arn
}

output "role_name" {
  description = "Name of the IRSA IAM role."
  value       = aws_iam_role.irsa.name
}
