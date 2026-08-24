# IRSA role for the sre-incident-agent ServiceAccount.
#
# DELIBERATELY LEAST-PRIVILEGE per SECURITY.md: the only permission granted
# is secretsmanager:GetSecretValue / DescribeSecret, scoped to the
# sre-incident-agent/* secret ARN prefix. No iam:*, no eks:* cluster-admin,
# no wildcard resource ("*") permissions of any kind, and specifically no
# permission to create or modify IAM roles/policies (including its own).
# This is the control that stops a "widen my own permissions" prompt-
# injection scenario from having anywhere to go even if an injected
# instruction were somehow followed by the agent — see
# docs/incidents/002-prompt-injection-attempt.md and docs/THREAT_MODEL.md in
# the application repo.

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "irsa_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:${var.k8s_namespace}:${var.k8s_service_account_name}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "irsa" {
  name               = "sre-incident-agent-${var.environment}-irsa"
  description        = "IRSA role for the sre-incident-agent Kubernetes ServiceAccount (system:serviceaccount:${var.k8s_namespace}:${var.k8s_service_account_name}). Least-privilege: Secrets Manager read-only on this workload's own secrets, nothing else."
  assume_role_policy = data.aws_iam_policy_document.irsa_trust.json

  # No permissions boundary elevation, no attached AWS managed admin
  # policies. Everything this role can do is defined explicitly below.
  tags = var.tags
}

data "aws_iam_policy_document" "secrets_read_only" {
  statement {
    sid    = "ReadOwnSecretsOnly"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.secrets_manager_prefix}/*",
    ]
  }

  # Explicitly no statement granting secretsmanager:PutSecretValue,
  # secretsmanager:CreateSecret, secretsmanager:DeleteSecret,
  # secretsmanager:ListSecrets (unscoped), kms:*, iam:*, or any "*"
  # resource. The agent only ever needs to READ its own pre-provisioned
  # secrets; it never creates, rotates, or deletes them.
}

resource "aws_iam_role_policy" "secrets_read_only" {
  name   = "secrets-read-only"
  role   = aws_iam_role.irsa.id
  policy = data.aws_iam_policy_document.secrets_read_only.json
}
