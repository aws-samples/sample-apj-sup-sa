output "aws_region" {
  description = "AWS region used by this deployment."
  value       = var.aws_region
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS cluster endpoint."
  value       = module.eks.cluster_endpoint
}

output "cluster_oidc_issuer_url" {
  description = "OIDC issuer URL for the EKS cluster."
  value       = module.eks.cluster_oidc_issuer_url
}

output "cluster_oidc_provider_arn" {
  description = "OIDC provider ARN for IRSA integrations."
  value       = module.eks.oidc_provider_arn
}

output "configure_kubectl" {
  description = "Command to configure kubectl for this cluster."
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

output "vpc_id" {
  description = "VPC ID used by the EKS cluster."
  value       = module.vpc.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs used by the EKS cluster and GPU node discovery."
  value       = module.vpc.private_subnets
}

output "node_security_group_id" {
  description = "EKS worker node security group ID, also selected by Karpenter nodes."
  value       = module.eks.node_security_group_id
}

output "osmo_namespace" {
  description = "Kubernetes namespace for OSMO services."
  value       = var.osmo_namespace
}

output "osmo_workload_namespace" {
  description = "Kubernetes namespace for OSMO workflow pods."
  value       = var.osmo_workload_namespace
}

output "osmo_service_account_name" {
  description = "Kubernetes service account used by OSMO services."
  value       = var.osmo_service_account_name
}

output "osmo_service_account_role_arn" {
  description = "IAM role ARN annotated on the OSMO service account."
  value       = aws_iam_role.osmo_service_account.arn
}

output "osmo_artifacts_bucket" {
  description = "S3 bucket for OSMO artifacts."
  value       = aws_s3_bucket.osmo.id
}

output "osmo_runtime_secret_arn" {
  description = "Secrets Manager secret consumed by infra/kubernetes/deploy-osmo.sh."
  value       = aws_secretsmanager_secret.osmo_runtime.arn
}

output "ecr_repository_url" {
  description = "ECR repository for future custom workflow images."
  value       = aws_ecr_repository.workloads.repository_url
}

output "karpenter_interruption_queue_name" {
  description = "SQS queue name consumed by Karpenter for interruption handling."
  value       = module.karpenter.queue_name
}

output "karpenter_node_iam_role_name" {
  description = "IAM role name used by Karpenter-provisioned worker nodes."
  value       = module.karpenter.node_iam_role_name
}
