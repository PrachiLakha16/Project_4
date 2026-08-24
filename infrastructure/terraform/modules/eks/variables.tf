variable "cluster_name" {
  description = "Name of the existing EKS cluster this workload is deployed onto. This module does not create a cluster; it only reads the existing one and its OIDC provider so downstream modules (iam) can build an IRSA trust policy."
  type        = string
}
