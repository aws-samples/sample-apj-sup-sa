# Optional: the usage dashboard behind an Application Load Balancer that signs people in with the IdP (OIDC) and
# serves only members of the admin groups (full access) and the viewer groups (read-only). Off unless
# dashboard_hostname is set; then the Function URL is removed so the ALB is the only way in.
#
# Two gates: the IdP app is assigned only to the admin and viewer groups (the IdP refuses everyone else at sign-in),
# and the dashboard Lambda verifies the ALB-signed x-amzn-oidc-data header and requires an admin or viewer group in
# its group claim (dashboard_group_claim, else group_claim). Assign the app to every group in
# dashboard_viewer_groups too, or viewers cannot sign in.
# ALB authenticate-oidc authenticates; it has no claim-based authorization of its own.
# https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html

variable "dashboard_group_claim" {
  description = "Claim in the IdP's userinfo (what the ALB passes to the dashboard) that lists the user's groups, when it differs from group_claim, which names the claim in the token the gateway checks. Empty = group_claim"
  type        = string
  default     = ""
}

variable "dashboard_hostname" {
  description = "FQDN for the dashboard ALB, in dashboard_zone_name (for example usage.example.com). Empty = no ALB, Function URL with AWS_IAM only."
  type        = string
  default     = ""
}

variable "dashboard_zone_name" {
  description = "Route 53 public hosted zone that holds dashboard_hostname (certificate validation and alias record)"
  type        = string
  default     = ""
}

variable "dashboard_allowed_cidrs" {
  description = "Source IPv4 CIDRs allowed to reach the dashboard ALB on 443: each /8 or narrower, and at most a /8's worth of addresses (16,777,216) in total, so a list can never add up to a large part of the internet"
  type        = list(string)
  default     = []
  validation {
    # IPv4 only (the rule sets cidr_ipv4), and no range wider than /8: two /1s would open the dashboard to everyone.
    condition = alltrue([for c in var.dashboard_allowed_cidrs : can(regex("^\\d{1,3}(\\.\\d{1,3}){3}/\\d{1,2}$", c)) && can(cidrhost(c, 0))
    && tonumber(split("/", c)[1]) >= 8])
    error_message = "dashboard_allowed_cidrs must be IPv4 CIDRs of /8 or narrower (for example 203.0.113.0/24)."
  }
  validation {
    # Together, too: a list of /8s would otherwise open the sign-in page to most of IPv4.
    condition     = sum(concat([0], [for c in var.dashboard_allowed_cidrs : can(regex("/\\d{1,2}$", c)) ? pow(2, 32 - tonumber(split("/", c)[1])) : 0])) <= 16777216
    error_message = "dashboard_allowed_cidrs may cover at most 16,777,216 addresses in total (one /8): list your offices' and VPN's ranges, not whole blocks."
  }
}

variable "dashboard_oidc_client_id" {
  description = "Client id of the IdP's confidential OIDC app for the dashboard: redirect URI https://<dashboard_hostname>/oauth2/idpresponse, client authentication client_secret_post (the ALB sends the secret in the token request body; with client_secret_basic the IdP rejects it and the ALB answers 561)"
  type        = string
  default     = ""
}

variable "dashboard_oidc_secret_name" {
  description = "Secrets Manager secret holding that app's client secret (plain string)"
  type        = string
  default     = "ocgw/dashboard/oidc"
}

variable "dashboard_oidc_issuer" {
  description = "OIDC issuer of the IdP"
  type        = string
  default     = "https://oauth.id.jumpcloud.com/"
}

variable "dashboard_oidc_endpoints" {
  description = "IdP endpoints (from <issuer>/.well-known/openid-configuration)"
  type        = object({ authorization = string, token = string, user_info = string })
  default = {
    authorization = "https://oauth.id.jumpcloud.com/oauth2/auth"
    token         = "https://oauth.id.jumpcloud.com/oauth2/token"
    user_info     = "https://oauth.id.jumpcloud.com/userinfo"
  }
}

variable "dashboard_session_seconds" {
  description = "ALB sign-in session length. Signing out of the IdP does not end it; /logout does."
  type        = number
  default     = 3600
}

variable "dashboard_private_role" {
  description = "Role of an IAM caller of the Function URL (private mode, no ALB): viewer (read-only, the default) or admin. Decided by the Lambda, so every client sees the same role. Keep viewer unless every principal allowed lambda:InvokeFunctionUrl on the function should administer budgets."
  type        = string
  default     = "viewer"
  validation {
    condition     = contains(["viewer", "admin"], var.dashboard_private_role)
    error_message = "dashboard_private_role must be viewer or admin."
  }
}

variable "dashboard_admin_groups" {
  description = "IdP groups (groups claim) with full dashboard access. Read-only groups go in dashboard_viewer_groups; assign the IdP app to both"
  type        = list(string)
  default     = ["ocgw-admin"]
}

locals {
  dashboard_alb = var.dashboard_hostname != ""
}

data "aws_vpc" "dashboard" {
  count   = local.dashboard_alb ? 1 : 0
  default = true
}

data "aws_subnets" "dashboard" {
  count = local.dashboard_alb ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.dashboard[0].id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_route53_zone" "dashboard" {
  count = local.dashboard_alb ? 1 : 0
  name  = var.dashboard_zone_name
}

data "aws_secretsmanager_secret_version" "dashboard_oidc" {
  count     = local.dashboard_alb ? 1 : 0
  secret_id = var.dashboard_oidc_secret_name
}

resource "aws_acm_certificate" "dashboard" {
  count             = local.dashboard_alb ? 1 : 0
  domain_name       = var.dashboard_hostname
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_route53_record" "dashboard_validation" {
  for_each = local.dashboard_alb ? { for o in aws_acm_certificate.dashboard[0].domain_validation_options : o.domain_name => o } : {}
  zone_id  = data.aws_route53_zone.dashboard[0].zone_id
  name     = each.value.resource_record_name
  type     = each.value.resource_record_type
  records  = [each.value.resource_record_value]
  ttl      = 60
}

resource "aws_acm_certificate_validation" "dashboard" {
  count                   = local.dashboard_alb ? 1 : 0
  certificate_arn         = aws_acm_certificate.dashboard[0].arn
  validation_record_fqdns = [for r in aws_route53_record.dashboard_validation : r.fqdn]
}

resource "aws_security_group" "dashboard_alb" {
  count       = local.dashboard_alb ? 1 : 0
  name        = "${var.name}-dashboard-alb"
  description = "Dashboard ALB: HTTPS from the allowed CIDRs only"
  vpc_id      = data.aws_vpc.dashboard[0].id
}

resource "aws_vpc_security_group_ingress_rule" "dashboard_https" {
  for_each          = local.dashboard_alb ? toset(var.dashboard_allowed_cidrs) : toset([])
  security_group_id = aws_security_group.dashboard_alb[0].id
  description       = "HTTPS from an allowed network"
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# The ALB calls the IdP's token and userinfo endpoints and invokes the Lambda; both need egress (HTTPS to the IdP).
resource "aws_vpc_security_group_egress_rule" "dashboard_https_out" {
  count             = local.dashboard_alb ? 1 : 0
  security_group_id = aws_security_group.dashboard_alb[0].id
  description       = "HTTPS to the IdP token and userinfo endpoints"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_lb" "dashboard" {
  #checkov:skip=CKV2_AWS_76:the attached web ACL runs AWSManagedRulesKnownBadInputsRuleSet, which includes the Log4JRCE rules (security.tf)
  count                      = local.dashboard_alb ? 1 : 0
  name                       = "${var.name}-dashboard"
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.dashboard_alb[0].id]
  subnets                    = data.aws_subnets.dashboard[0].ids
  drop_invalid_header_fields = true
  enable_deletion_protection = var.dashboard_deletion_protection
  access_logs {
    bucket  = aws_s3_bucket.logs.id
    prefix  = "alb"
    enabled = true
  }
  depends_on = [aws_s3_bucket_policy.logs]
}

resource "aws_lb_target_group" "dashboard" {
  count       = local.dashboard_alb ? 1 : 0
  name        = "${var.name}-dashboard"
  target_type = "lambda"
  # /logout returns one Set-Cookie per ALB session-cookie shard, which needs multi-value headers.
  lambda_multi_value_headers_enabled = true
}

resource "aws_lambda_permission" "dashboard_alb" {
  count         = local.dashboard_alb ? 1 : 0
  statement_id  = "AllowDashboardAlb"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dashboard.function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.dashboard[0].arn
}

resource "aws_lb_target_group_attachment" "dashboard" {
  count            = local.dashboard_alb ? 1 : 0
  target_group_arn = aws_lb_target_group.dashboard[0].arn
  target_id        = aws_lambda_function.dashboard.arn
  depends_on       = [aws_lambda_permission.dashboard_alb]
}

resource "aws_lb_listener" "dashboard" {
  count             = local.dashboard_alb ? 1 : 0
  load_balancer_arn = aws_lb.dashboard[0].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.dashboard[0].certificate_arn
  # HSTS on every answer, including the ALB's own sign-in redirects and WAF blocks, which never reach the Lambda (r197).
  routing_http_response_strict_transport_security_header_value = "max-age=31536000"

  default_action {
    type  = "authenticate-oidc"
    order = 1
    authenticate_oidc {
      issuer                     = var.dashboard_oidc_issuer
      authorization_endpoint     = var.dashboard_oidc_endpoints.authorization
      token_endpoint             = var.dashboard_oidc_endpoints.token
      user_info_endpoint         = var.dashboard_oidc_endpoints.user_info
      client_id                  = var.dashboard_oidc_client_id
      client_secret              = data.aws_secretsmanager_secret_version.dashboard_oidc[0].secret_string
      scope                      = "openid email profile"
      session_timeout            = var.dashboard_session_seconds
      on_unauthenticated_request = "authenticate"
    }
  }
  default_action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.dashboard[0].arn
  }
}

# After /logout the browser lands here. It must not be behind authenticate-oidc, or the still-valid IdP session would
# sign the person straight back in.
resource "aws_lb_listener_rule" "dashboard_signed_out" {
  count        = local.dashboard_alb ? 1 : 0
  listener_arn = aws_lb_listener.dashboard[0].arn
  priority     = 10
  condition {
    path_pattern { values = ["/signed-out"] }
  }
  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/html"
      status_code  = "200"
      message_body = "<!doctype html><meta charset=utf-8><title>Signed out</title><body style=\"font:14px system-ui;margin:48px\"><h1 style=\"font-size:20px\">You are signed out of the usage dashboard</h1><p>To end your identity provider session as well, sign out there too.</p><p><a href=\"/\">Sign in again</a></p></body>"
    }
  }
}

resource "aws_route53_record" "dashboard" {
  count   = local.dashboard_alb ? 1 : 0
  zone_id = data.aws_route53_zone.dashboard[0].zone_id
  name    = var.dashboard_hostname
  type    = "A"
  alias {
    name                   = aws_lb.dashboard[0].dns_name
    zone_id                = aws_lb.dashboard[0].zone_id
    evaluate_target_health = false
  }
}

output "dashboard_alb_url" { value = local.dashboard_alb ? "https://${var.dashboard_hostname}/" : null }
