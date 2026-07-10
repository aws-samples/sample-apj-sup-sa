variable "aws_region" {
  description = "AWS region for the target EKS cluster and ALB."
  type        = string
  default     = "ap-northeast-2"
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

variable "vpc_id" {
  description = "VPC ID used by the target EKS cluster."
  type        = string
}

variable "domain_name" {
  description = "Fully qualified domain name for the OSMO admin UI, for example osmo.example.com."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", trimsuffix(var.domain_name, ".")))
    error_message = "domain_name must be a non-empty fully qualified domain name, for example osmo.example.com."
  }
}

variable "hosted_zone_id" {
  description = "Route 53 hosted zone ID that owns domain_name."
  type        = string
  nullable    = false

  validation {
    condition     = length(trimspace(var.hosted_zone_id)) > 0
    error_message = "hosted_zone_id must be set."
  }
}

variable "allowed_cidrs" {
  description = "CIDR ranges allowed to access the OSMO admin ALB. Must not include 0.0.0.0/0."
  type        = list(string)
  nullable    = false

  validation {
    condition     = length(var.allowed_cidrs) > 0 && alltrue([for cidr in var.allowed_cidrs : can(cidrhost(cidr, 0))]) && !contains(var.allowed_cidrs, "0.0.0.0/0")
    error_message = "allowed_cidrs must be non-empty valid CIDRs and must not include 0.0.0.0/0."
  }
}

variable "certificate_subject_alternative_names" {
  description = "Optional ACM certificate subject alternative names."
  type        = list(string)
  default     = []
}

variable "aws_load_balancer_controller_chart_version" {
  description = "Helm chart version for AWS Load Balancer Controller."
  type        = string
  default     = "3.2.2"
}

variable "ingress_class_name" {
  description = "Kubernetes IngressClass name handled by AWS Load Balancer Controller."
  type        = string
  default     = "alb"
}

variable "load_balancer_scheme" {
  description = "ALB scheme for the admin ingress."
  type        = string
  default     = "internet-facing"

  validation {
    condition     = contains(["internet-facing", "internal"], var.load_balancer_scheme)
    error_message = "load_balancer_scheme must be internet-facing or internal."
  }
}

variable "load_balancer_name" {
  description = "Optional ALB name override. Must be 32 characters or fewer when set."
  type        = string
  default     = null

  validation {
    condition     = var.load_balancer_name == null || length(var.load_balancer_name) <= 32
    error_message = "load_balancer_name must be 32 characters or fewer."
  }
}

variable "osmo_namespace" {
  description = "Kubernetes namespace containing the OSMO UI service."
  type        = string
  default     = "osmo"
}

variable "osmo_ui_service_name" {
  description = "Kubernetes service name for OSMO UI."
  type        = string
  default     = "osmo-ui"
}

variable "osmo_ui_service_port" {
  description = "Kubernetes service port number for OSMO UI."
  type        = number
  default     = 80
}

variable "healthcheck_path" {
  description = "ALB health check path for OSMO UI."
  type        = string
  default     = "/"
}

variable "kubeconfig_path" {
  description = "Path to kubeconfig used by Terraform's Helm and Kubernetes providers."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Optional kubeconfig context. Leave null to use the current context."
  type        = string
  default     = null
}

variable "tags" {
  description = "Additional tags applied to taggable ingress resources."
  type        = map(string)
  default     = {}
}
