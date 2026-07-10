output "amp_workspace_id" {
  description = "Amazon Managed Service for Prometheus workspace ID."
  value       = aws_prometheus_workspace.osmo.id
}

output "amp_workspace_arn" {
  description = "Amazon Managed Service for Prometheus workspace ARN."
  value       = aws_prometheus_workspace.osmo.arn
}

output "amp_prometheus_endpoint" {
  description = "AMP Prometheus-compatible query endpoint."
  value       = aws_prometheus_workspace.osmo.prometheus_endpoint
}

output "amp_remote_write_url" {
  description = "Prometheus remote_write URL for AMP."
  value       = local.amp_remote_write_url
}

output "prometheus_remote_write_role_arn" {
  description = "IRSA role ARN used by in-cluster Prometheus to remote_write to AMP."
  value       = aws_iam_role.prometheus_remote_write.arn
}

output "monitoring_namespace" {
  description = "Namespace containing kube-prometheus-stack."
  value       = var.monitoring_namespace
}

output "prometheus_release_name" {
  description = "kube-prometheus-stack Helm release name."
  value       = helm_release.kube_prometheus_stack.name
}

output "prometheus_service_account_name" {
  description = "Prometheus service account annotated with the AMP remote_write role."
  value       = local.prometheus_service_account_name
}

output "amg_workspace_id" {
  description = "Amazon Managed Grafana workspace ID."
  value       = aws_grafana_workspace.osmo.id
}

output "amg_workspace_url" {
  description = "Amazon Managed Grafana workspace URL."
  value       = "https://${aws_grafana_workspace.osmo.endpoint}"
}

output "amg_authentication_provider" {
  description = "AMG authentication mode. Use IAM Identity Center users or groups, not a local id/password."
  value       = "AWS_SSO"
}

output "grafana_provisioner_service_account_id" {
  description = "AMG service account ID used by infra/observability/deploy.sh to create a short-lived API token."
  value       = aws_grafana_workspace_service_account.provisioner.service_account_id
}

output "grafana_datasource_name" {
  description = "AMG Prometheus data source name provisioned by infra/observability/deploy.sh."
  value       = var.grafana_datasource_name
}

output "grafana_datasource_uid" {
  description = "AMG Prometheus data source UID provisioned by infra/observability/deploy.sh."
  value       = var.grafana_datasource_uid
}
