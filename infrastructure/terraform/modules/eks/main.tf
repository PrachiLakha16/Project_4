# References an EXISTING EKS cluster and its IAM OIDC provider. This
# workload does not own cluster lifecycle (that belongs to a separate
# platform/landing-zone stack) — it only needs the cluster's OIDC issuer to
# build an IRSA trust policy in modules/iam, and the cluster endpoint/CA for
# any provider configuration that needs to talk to the Kubernetes API
# directly (e.g. a helm_release or kubernetes_* resource elsewhere).

data "aws_eks_cluster" "this" {
  name = var.cluster_name
}

data "aws_eks_cluster_auth" "this" {
  name = var.cluster_name
}

# The cluster's OIDC issuer URL, stripped of its https:// scheme, as used in
# IAM OIDC provider ARNs and trust policy StringEquals conditions.
locals {
  oidc_issuer_host = replace(data.aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")
}

# Look up the IAM OIDC provider that must already be associated with this
# cluster (created once per cluster, typically by the platform stack that
# provisions the cluster itself via `aws eks update-kubeconfig` /
# `eksctl utils associate-iam-oidc-provider` or the equivalent Terraform
# resource in the landing-zone stack). We only read it here.
data "aws_iam_openid_connect_provider" "this" {
  url = data.aws_eks_cluster.this.identity[0].oidc[0].issuer
}
