# X-Ray tracing on every Lambda (tracing_config Active): the execution roles need the daemon write permissions.
resource "aws_iam_role_policy_attachment" "xray" {
  for_each   = merge({ dashboard = aws_iam_role.dashboard.name, interceptor = aws_iam_role.interceptor.name, usage_sync = aws_iam_role.usage_sync.name })
  role       = each.value
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# Access-log bucket: ALB access logs (alb/). SSE-S3 because
# ALB log delivery does not support SSE-KMS; kept 90 days.
resource "aws_s3_bucket" "logs" {
  #checkov:skip=CKV_AWS_18:this is the access-log bucket itself; logging it to itself would loop
  #checkov:skip=CKV_AWS_144:single-region POC; log copies in a second region are not required
  #checkov:skip=CKV_AWS_145:ALB access log delivery only supports SSE-S3 (AES256), not SSE-KMS
  #checkov:skip=CKV2_AWS_62:no consumer needs object-created events on access logs
  bucket        = "${var.name}-logs-${local.acct}-${var.region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration { days = 90 }
    noncurrent_version_expiration { noncurrent_days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

data "aws_elb_service_account" "this" {}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*",
      Resource = [aws_s3_bucket.logs.arn, "${aws_s3_bucket.logs.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } },
      { Sid = "AlbAccessLogs", Effect = "Allow", Principal = { AWS = data.aws_elb_service_account.this.arn },
      Action = "s3:PutObject", Resource = "${aws_s3_bucket.logs.arn}/alb/AWSLogs/${local.acct}/*" },
    ]
  })
  depends_on = [aws_s3_bucket_public_access_block.logs]
}

variable "dashboard_deletion_protection" {
  description = "Deletion protection on the dashboard ALB. Set false (and apply) before terraform destroy."
  type        = bool
  default     = true
}

variable "ledger_deletion_protection" {
  description = "Deletion protection on the DynamoDB ledger (spend, policy and the audit log). Set false (and apply) before terraform destroy."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the stack's log groups (365 = one year)."
  type        = number
  default     = 365
}

# One customer-managed key for the stack's data at rest: CloudWatch log groups, Lambda environment variables and the
# DynamoDB usage table. Rotated yearly.
resource "aws_kms_key" "main" {
  description             = "${var.name}: logs, Lambda environment, DynamoDB usage ledger"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # The default key-administration delegation: kms:* for the account root lets IAM policies in this account
      # manage and use this key (Resource "*" in a key policy is this key only). For production, narrow key
      # administration and key use to named IAM roles.
      { Sid = "AccountAdmin", Effect = "Allow", Principal = { AWS = "arn:aws:iam::${local.acct}:root" }, Action = "kms:*", Resource = "*" },
      { Sid    = "CloudWatchLogs", Effect = "Allow", Principal = { Service = "logs.${var.region}.amazonaws.com" },
        Action = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"], Resource = "*",
      Condition = { ArnLike = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.region}:${local.acct}:log-group:*" } } },
    ]
  })
}

resource "aws_kms_alias" "ocgw" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.main.key_id
}

# Every Lambda role uses the key: environment variables (Lambda decrypts them as the function role) and the
# DynamoDB usage table (reads and writes need Decrypt / GenerateDataKey).
resource "aws_iam_role_policy" "kms" {
  for_each = merge({ dashboard = aws_iam_role.dashboard.id, interceptor = aws_iam_role.interceptor.id, usage_sync = aws_iam_role.usage_sync.id })
  name     = "kms-ocgw"
  role     = each.value
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"], Resource = aws_kms_key.main.arn }]
  })
}

# WAF on the internet-facing dashboard ALB: AWS managed common + known-bad-inputs rule groups and a per-IP rate limit.
# The OIDC callback and the API are small requests; SizeRestrictions_BODY is counted only, in case a large policy save.
# SizeRestrictions_Cookie_HEADER (10 KB) is counted only too: the ALB's OIDC session cookie is split into up to 4
# shards (about 16 KB) for an IdP that issues large tokens or many groups, and blocking it would lock that admin out.
resource "aws_wafv2_web_acl" "dashboard" {
  count = local.dashboard_alb ? 1 : 0
  name  = "${var.name}-dashboard"
  scope = "REGIONAL"
  default_action {
    allow {}
  }
  rule {
    name     = "common"
    priority = 10
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }
        rule_action_override {
          name = "SizeRestrictions_Cookie_HEADER"
          action_to_use {
            count {}
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-common"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "known-bad-inputs"
    priority = 20
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-knownbad"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "rate-per-ip"
    priority = 30
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 1000
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate"
      sampled_requests_enabled   = true
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name}-dashboard"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "dashboard" {
  count        = local.dashboard_alb ? 1 : 0
  resource_arn = aws_lb.dashboard[0].arn
  web_acl_arn  = aws_wafv2_web_acl.dashboard[0].arn
}

# WAF request logs (name must start with aws-waf-logs-); session cookies and the Authorization header are redacted.
resource "aws_cloudwatch_log_group" "waf" {
  count             = local.dashboard_alb ? 1 : 0
  name              = "aws-waf-logs-${var.name}-dashboard"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

resource "aws_wafv2_web_acl_logging_configuration" "dashboard" {
  count                   = local.dashboard_alb ? 1 : 0
  resource_arn            = aws_wafv2_web_acl.dashboard[0].arn
  log_destination_configs = [aws_cloudwatch_log_group.waf[0].arn]
  redacted_fields {
    single_header {
      name = "cookie"
    }
  }
  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
}
