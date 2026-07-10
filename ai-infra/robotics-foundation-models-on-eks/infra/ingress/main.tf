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
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.36"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = var.tags
  }
}

provider "helm" {
  kubernetes {
    config_path    = pathexpand(var.kubeconfig_path)
    config_context = var.kube_context
  }
}

provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig_path)
  config_context = var.kube_context
}

data "aws_partition" "current" {}

locals {
  domain_name          = trimsuffix(var.domain_name, ".")
  service_account_name = "aws-load-balancer-controller"
  ingress_name         = "osmo-admin"
  ingress_namespace    = var.osmo_namespace
  raw_lb_name          = replace(lower("${var.cluster_name}-admin"), "/[^a-z0-9-]/", "-")
  load_balancer_name   = coalesce(var.load_balancer_name, substr(local.raw_lb_name, 0, 32))
}

data "aws_iam_policy_document" "aws_load_balancer_controller_assume_role" {
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
      values   = ["system:serviceaccount:kube-system:${local.service_account_name}"]
    }
  }
}

resource "aws_iam_role" "aws_load_balancer_controller" {
  name               = substr("${var.cluster_name}-lbc", 0, 64)
  assume_role_policy = data.aws_iam_policy_document.aws_load_balancer_controller_assume_role.json

  tags = {
    Name = substr("${var.cluster_name}-lbc", 0, 64)
  }
}

resource "aws_iam_policy" "aws_load_balancer_controller" {
  name        = substr("${var.cluster_name}-lbc", 0, 128)
  description = "IAM policy for AWS Load Balancer Controller on ${var.cluster_name}"
  policy      = file("${path.module}/aws-load-balancer-controller-iam-policy.json")
}

resource "aws_iam_role_policy_attachment" "aws_load_balancer_controller" {
  role       = aws_iam_role.aws_load_balancer_controller.name
  policy_arn = aws_iam_policy.aws_load_balancer_controller.arn
}

resource "helm_release" "aws_load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = var.aws_load_balancer_controller_chart_version

  wait          = true
  wait_for_jobs = true
  timeout       = 600

  values = [
    yamlencode({
      clusterName = var.cluster_name
      region      = var.aws_region
      vpcId       = var.vpc_id

      ingressClass               = var.ingress_class_name
      createIngressClassResource = true
      defaultTargetType          = "ip"
      defaultLoadBalancerScheme  = var.load_balancer_scheme

      serviceAccount = {
        create = true
        name   = local.service_account_name
        annotations = {
          "eks.amazonaws.com/role-arn" = aws_iam_role.aws_load_balancer_controller.arn
        }
      }
    })
  ]

  depends_on = [aws_iam_role_policy_attachment.aws_load_balancer_controller]
}

resource "aws_acm_certificate" "osmo_admin" {
  domain_name               = local.domain_name
  subject_alternative_names = var.certificate_subject_alternative_names
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = local.domain_name
  }
}

resource "aws_route53_record" "certificate_validation" {
  for_each = {
    for option in aws_acm_certificate.osmo_admin.domain_validation_options : option.domain_name => {
      name   = option.resource_record_name
      record = option.resource_record_value
      type   = option.resource_record_type
    }
  }

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = var.hosted_zone_id
}

resource "aws_acm_certificate_validation" "osmo_admin" {
  certificate_arn         = aws_acm_certificate.osmo_admin.arn
  validation_record_fqdns = [for record in aws_route53_record.certificate_validation : record.fqdn]
}

resource "kubernetes_ingress_v1" "osmo_admin" {
  wait_for_load_balancer = true

  metadata {
    name      = local.ingress_name
    namespace = local.ingress_namespace
    annotations = {
      "alb.ingress.kubernetes.io/backend-protocol"         = "HTTP"
      "alb.ingress.kubernetes.io/certificate-arn"          = aws_acm_certificate_validation.osmo_admin.certificate_arn
      "alb.ingress.kubernetes.io/healthcheck-path"         = var.healthcheck_path
      "alb.ingress.kubernetes.io/inbound-cidrs"            = join(",", var.allowed_cidrs)
      "alb.ingress.kubernetes.io/listen-ports"             = jsonencode([{ HTTP = 80 }, { HTTPS = 443 }])
      "alb.ingress.kubernetes.io/load-balancer-attributes" = "idle_timeout.timeout_seconds=3600,routing.http2.enabled=true"
      "alb.ingress.kubernetes.io/load-balancer-name"       = local.load_balancer_name
      "alb.ingress.kubernetes.io/scheme"                   = var.load_balancer_scheme
      "alb.ingress.kubernetes.io/ssl-redirect"             = "443"
      "alb.ingress.kubernetes.io/success-codes"            = "200-399"
      "alb.ingress.kubernetes.io/target-type"              = "ip"
    }
    labels = {
      "app.kubernetes.io/name"       = "osmo-admin-ingress"
      "app.kubernetes.io/part-of"    = "robotics-foundation-models-on-eks"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    ingress_class_name = var.ingress_class_name

    rule {
      host = local.domain_name

      http {
        path {
          path      = "/"
          path_type = "Prefix"

          backend {
            service {
              name = var.osmo_ui_service_name

              port {
                number = var.osmo_ui_service_port
              }
            }
          }
        }
      }
    }
  }

  depends_on = [
    helm_release.aws_load_balancer_controller,
    aws_acm_certificate_validation.osmo_admin
  ]
}

data "aws_lb" "osmo_admin" {
  name = local.load_balancer_name

  depends_on = [kubernetes_ingress_v1.osmo_admin]
}

resource "aws_route53_record" "osmo_admin" {
  name    = local.domain_name
  type    = "A"
  zone_id = var.hosted_zone_id

  alias {
    evaluate_target_health = true
    name                   = data.aws_lb.osmo_admin.dns_name
    zone_id                = data.aws_lb.osmo_admin.zone_id
  }
}
