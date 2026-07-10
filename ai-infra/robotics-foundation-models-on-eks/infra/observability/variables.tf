variable "aws_region" {
  description = "AWS region for the target EKS cluster, AMP workspace, and AMG workspace."
  type        = string
  default     = "ap-northeast-2"
}

variable "name_prefix" {
  description = "Name prefix for observability resources. Defaults to the core cluster name when deployed through infra/observability/deploy.sh."
  type        = string
  default     = "example-osmo-eks"
}

variable "cluster_name" {
  description = "Existing EKS cluster name."
  type        = string
}

variable "cluster_oidc_issuer_url" {
  description = "OIDC issuer URL from the core Terraform output or EKS describe-cluster."
  type        = string
}

variable "cluster_oidc_provider_arn" {
  description = "OIDC provider ARN from the core Terraform output or IAM OIDC provider."
  type        = string
}

variable "monitoring_namespace" {
  description = "Kubernetes namespace for the Prometheus stack."
  type        = string
  default     = "monitoring"
}

variable "prometheus_release_name" {
  description = "Helm release name for kube-prometheus-stack."
  type        = string
  default     = "aws-osmo-observability"
}

variable "prometheus_service_account_name" {
  description = "Optional service account name for the Prometheus server that remote writes to AMP."
  type        = string
  default     = null
}

variable "kube_prometheus_stack_chart_version" {
  description = "prometheus-community/kube-prometheus-stack chart version."
  type        = string
  default     = "84.5.0"
}

variable "prometheus_retention" {
  description = "Short local Prometheus retention. AMP is the long-term metrics backend."
  type        = string
  default     = "6h"
}

variable "enable_alertmanager" {
  description = "Install Alertmanager with kube-prometheus-stack."
  type        = bool
  default     = false
}

variable "amp_workspace_alias" {
  description = "Optional AMP workspace alias."
  type        = string
  default     = null
}

variable "amg_workspace_name" {
  description = "Optional AMG workspace name."
  type        = string
  default     = null
}

variable "grafana_version" {
  description = "Amazon Managed Grafana major/minor version."
  type        = string
  default     = "10.4"
}

variable "grafana_datasource_name" {
  description = "Prometheus data source name created in AMG by infra/observability/deploy.sh."
  type        = string
  default     = "AMP aws-osmo"
}

variable "grafana_datasource_uid" {
  description = "Prometheus data source UID created in AMG by infra/observability/deploy.sh."
  type        = string
  default     = "aws-osmo-amp"
}

variable "grafana_provisioner_service_account_name" {
  description = "AMG service account used by infra/observability/deploy.sh for data source and dashboard provisioning."
  type        = string
  default     = "aws-osmo-observability-provisioner"
}

variable "admin_user_ids" {
  description = "IAM Identity Center user IDs granted AMG Admin. AMG does not create a local username/password."
  type        = set(string)
  default     = []
}

variable "admin_group_ids" {
  description = "IAM Identity Center group IDs granted AMG Admin. AMG does not create a local username/password."
  type        = set(string)
  default     = []
}

variable "editor_group_ids" {
  description = "IAM Identity Center group IDs granted AMG Editor."
  type        = set(string)
  default     = []
}

variable "viewer_group_ids" {
  description = "IAM Identity Center group IDs granted AMG Viewer."
  type        = set(string)
  default     = []
}

variable "kubeconfig_path" {
  description = "Path to kubeconfig used by Terraform's Helm provider."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Optional kubeconfig context. Leave null to use the current context."
  type        = string
  default     = null
}

variable "tags" {
  description = "Additional tags applied to taggable observability resources."
  type        = map(string)
  default     = {}
}
