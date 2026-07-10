terraform {
  required_version = ">= 1.5.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.17"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

provider "helm" {
  kubernetes {
    config_path    = pathexpand(var.kubeconfig_path)
    config_context = var.kube_context
  }
}

locals {
  name_prefix                     = substr(replace(lower(var.name_prefix), "/[^a-z0-9-]/", "-"), 0, 40)
  tags                            = merge(var.tags, { ManagedBy = "terraform", Reference = "aws-osmo" })
  amp_alias                       = coalesce(var.amp_workspace_alias, "${local.name_prefix}-observability")
  amg_workspace_name              = coalesce(var.amg_workspace_name, "${local.name_prefix}-observability")
  prometheus_service_account_name = coalesce(var.prometheus_service_account_name, "${var.prometheus_release_name}-amp-remote-write")
  amp_remote_write_url            = "${trimsuffix(aws_prometheus_workspace.osmo.prometheus_endpoint, "/")}/api/v1/remote_write"
}

resource "aws_prometheus_workspace" "osmo" {
  alias = local.amp_alias

  tags = {
    Name = local.amp_alias
  }
}

data "aws_iam_policy_document" "prometheus_assume_role" {
  statement {
    effect = "Allow"

    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.cluster_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(var.cluster_oidc_issuer_url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(var.cluster_oidc_issuer_url, "https://", "")}:sub"
      values   = ["system:serviceaccount:${var.monitoring_namespace}:${local.prometheus_service_account_name}"]
    }
  }
}

resource "aws_iam_role" "prometheus_remote_write" {
  name               = substr("${local.name_prefix}-amp-ingest", 0, 64)
  assume_role_policy = data.aws_iam_policy_document.prometheus_assume_role.json

  tags = {
    Name = substr("${local.name_prefix}-amp-ingest", 0, 64)
  }
}

data "aws_iam_policy_document" "prometheus_remote_write" {
  statement {
    effect = "Allow"

    actions = [
      "aps:RemoteWrite"
    ]

    resources = [aws_prometheus_workspace.osmo.arn]
  }
}

resource "aws_iam_policy" "prometheus_remote_write" {
  name        = substr("${local.name_prefix}-amp-ingest", 0, 128)
  description = "Allow in-cluster Prometheus to remote_write OSMO metrics to AMP"
  policy      = data.aws_iam_policy_document.prometheus_remote_write.json
}

resource "aws_iam_role_policy_attachment" "prometheus_remote_write" {
  role       = aws_iam_role.prometheus_remote_write.name
  policy_arn = aws_iam_policy.prometheus_remote_write.arn
}

resource "helm_release" "kube_prometheus_stack" {
  name             = var.prometheus_release_name
  namespace        = var.monitoring_namespace
  create_namespace = true
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = var.kube_prometheus_stack_chart_version

  wait          = true
  wait_for_jobs = true
  timeout       = 900

  values = [
    yamlencode({
      fullnameOverride = var.prometheus_release_name

      alertmanager = {
        enabled = var.enable_alertmanager
      }

      grafana = {
        enabled = false
      }

      prometheus = {
        serviceAccount = {
          create = true
          name   = local.prometheus_service_account_name
          annotations = {
            "eks.amazonaws.com/role-arn" = aws_iam_role.prometheus_remote_write.arn
          }
        }

        prometheusSpec = {
          retention = var.prometheus_retention
          externalLabels = {
            cluster = var.cluster_name
          }
          podMonitorSelectorNilUsesHelmValues     = false
          serviceMonitorSelectorNilUsesHelmValues = false
          remoteWrite = [
            {
              url = local.amp_remote_write_url
              sigv4 = {
                region = var.aws_region
              }
              queueConfig = {
                capacity          = 2500
                maxSamplesPerSend = 1000
                maxShards         = 20
              }
            }
          ]
        }
      }
    })
  ]

  depends_on = [aws_iam_role_policy_attachment.prometheus_remote_write]
}

resource "aws_grafana_workspace" "osmo" {
  name                     = local.amg_workspace_name
  description              = "Amazon Managed Grafana workspace for ${var.cluster_name} OSMO observability"
  account_access_type      = "CURRENT_ACCOUNT"
  authentication_providers = ["AWS_SSO"]
  data_sources             = ["PROMETHEUS"]
  permission_type          = "SERVICE_MANAGED"
  grafana_version          = var.grafana_version

  tags = {
    Name = local.amg_workspace_name
  }
}

resource "aws_grafana_role_association" "admin_users" {
  count = length(var.admin_user_ids) > 0 ? 1 : 0

  role         = "ADMIN"
  user_ids     = var.admin_user_ids
  workspace_id = aws_grafana_workspace.osmo.id
}

resource "aws_grafana_role_association" "admin_groups" {
  count = length(var.admin_group_ids) > 0 ? 1 : 0

  group_ids    = var.admin_group_ids
  role         = "ADMIN"
  workspace_id = aws_grafana_workspace.osmo.id
}

resource "aws_grafana_role_association" "editor_groups" {
  count = length(var.editor_group_ids) > 0 ? 1 : 0

  group_ids    = var.editor_group_ids
  role         = "EDITOR"
  workspace_id = aws_grafana_workspace.osmo.id
}

resource "aws_grafana_role_association" "viewer_groups" {
  count = length(var.viewer_group_ids) > 0 ? 1 : 0

  group_ids    = var.viewer_group_ids
  role         = "VIEWER"
  workspace_id = aws_grafana_workspace.osmo.id
}

resource "aws_grafana_workspace_service_account" "provisioner" {
  grafana_role = "ADMIN"
  name         = var.grafana_provisioner_service_account_name
  workspace_id = aws_grafana_workspace.osmo.id
}
