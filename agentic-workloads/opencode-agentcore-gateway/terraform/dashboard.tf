# Usage dashboard: one Lambda serving the built UI and its /api/v2 API. It reads the ledger and, for admins, writes
# limits, boosts, policies and audit rows (dashboard_write below), and reads the gateway's rate limits. It sits behind a Function URL with
# AuthType AWS_IAM. No anonymous access: callers must sign with SigV4 and hold lambda:InvokeFunctionUrl on it, and
# people open it through dashboard/proxy.py, which listens on 127.0.0.1 only. With dashboard_hostname set, the Function
# URL is replaced by an ALB with IdP sign-in for the admin and viewer groups (dashboard_alb.tf).

variable "dashboard_read_enabled" {
  description = "Attach the dashboard role's table read policy (default true). For a first deploy you may apply it false, check that unsigned calls to the Function URL are refused, then apply true."
  type        = bool
  default     = true
}

resource "aws_iam_role" "dashboard" {
  name = "${var.name}-dashboard"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

# Logs to this function's own log group only (Terraform creates it), not every group in the account.
resource "aws_iam_role_policy" "dashboard_logs" {
  role = aws_iam_role.dashboard.id
  name = "own-logs"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
    Resource = "${aws_cloudwatch_log_group.dashboard.arn}:*" }]
  })
}

resource "aws_iam_role_policy" "dashboard_read" {
  count = var.dashboard_read_enabled ? 1 : 0
  role  = aws_iam_role.dashboard.id
  name  = "ledger-read-only"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["dynamodb:Scan", "dynamodb:Query", "dynamodb:GetItem", "dynamodb:BatchGetItem"], Resource = aws_dynamodb_table.usage.arn },
    { Effect = "Allow", Action = ["dynamodb:Query"], Resource = "${aws_dynamodb_table.usage.arn}/index/by_sk" }]
  })
}

locals {
  # The built Cloudscape app (make -C dashboard/ui-src build) ships inside the Lambda zip; text files only.
  dashboard_ui_dir       = "${path.module}/../lambda/dashboard_ui"
  dashboard_ui_files     = [for f in fileset(local.dashboard_ui_dir, "**") : f if can(regex("\\.(html|js|css|svg|json|txt)$", f))]
  dashboard_lambda_files = { for f in ["dashboard.py", "dashboard_api.py", "policy.py"] : f => file("${path.module}/../lambda/${f}") }
  model_prices           = { for k, m in var.openweight_models : "ow/${k}" => { input = m.in, output = m.out } }
}

data "archive_file" "dashboard" {
  type        = "zip"
  output_path = "${path.module}/build/dashboard.zip"
  dynamic "source" {
    for_each = local.dashboard_lambda_files
    content {
      content  = source.value
      filename = source.key
    }
  }
  dynamic "source" {
    for_each = toset(local.dashboard_ui_files)
    content {
      content  = file("${local.dashboard_ui_dir}/${source.value}")
      filename = "dashboard_ui/${source.value}"
    }
  }
}

variable "dashboard_viewer_groups" {
  description = "IdP groups that may view the dashboard read-only (admins in dashboard_admin_groups can also change limits)"
  type        = list(string)
  default     = []
}

# Writes from the dashboard: policy items, user overrides and boosts, and the audit trail. Reads come only from
# dashboard_read, so dashboard_read_enabled=false really leaves the data unreachable. On USER#<u> partitions the role
# may only UpdateItem the override and boost attributes of the BUDGET row: IAM cannot match the sort key, so
# dynamodb:Attributes keeps it off the usage counters the interceptor and usage sync write (pt_*, m|<model>|*, requests).
# The two spend attributes are listed for the audited repair (POST /api/v2/users/<u>/repair), which only REMOVEs one
# that the gateway cannot read, under a condition that it is still unreadable (dashboard_api.repair_window).
resource "aws_iam_role_policy" "dashboard_write" {
  role = aws_iam_role.dashboard.id
  name = "ledger-policy-write"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"], Resource = aws_dynamodb_table.usage.arn,
      Condition = { "ForAllValues:StringEquals" = { "dynamodb:LeadingKeys" = ["POLICY"] } } },
      # Audit rows: PutItem only here (no update or delete). dashboard_api._audit_puts conditions every Put on
      # attribute_not_exists(sk); IAM cannot require that condition, so it is the code, not this grant, that keeps a
      # row from being replaced.
      { Effect = "Allow", Action = ["dynamodb:PutItem"], Resource = aws_dynamodb_table.usage.arn,
        # AUDIT holds every row; AUDIT#<target> holds a copy per target, so one user's history is one bounded Query.
      Condition = { "ForAllValues:StringLike" = { "dynamodb:LeadingKeys" = ["AUDIT", "AUDIT#*"] } } },
      { Effect = "Allow", Action = ["dynamodb:UpdateItem"], Resource = aws_dynamodb_table.usage.arn,
        Condition = {
          "ForAllValues:StringLike"   = { "dynamodb:LeadingKeys" = ["USER#*"] }
          "ForAllValues:StringEquals" = { "dynamodb:Attributes" = ["pk", "sk", "budget_usd", "daily_usd", "hourly_usd", "rpm", "boost_usd", "boost_period", "boost_until", "boost_reason", "spend_usd", "model_spend_usd"] }
      } },
      # Each Put/Update in a transaction is also authorized as that action (the statements above); this grant is bounded
      # to the same partitions so it cannot widen them.
      { Effect = "Allow", Action = ["dynamodb:TransactWriteItems"], Resource = aws_dynamodb_table.usage.arn,
      Condition = { "ForAllValues:StringLike" = { "dynamodb:LeadingKeys" = ["POLICY", "AUDIT", "AUDIT#*", "USER#*"] } } },
      # Gateway rate limits are set in Terraform; the dashboard only reads them.
      { Effect = "Allow", Action = ["bedrock-agentcore:ListGatewayRateLimits", "bedrock-agentcore:GetGatewayRateLimit"],
      Resource = [aws_bedrockagentcore_gateway.gw.gateway_arn, "${aws_bedrockagentcore_gateway.gw.gateway_arn}/*"] },
    ]
  })
}

resource "aws_cloudwatch_log_group" "dashboard" {
  name              = "/aws/lambda/${var.name}-dashboard"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

resource "aws_lambda_function" "dashboard" {
  #checkov:skip=CKV_AWS_115:reserved concurrency would carve capacity out of the account pool; only admin and viewer principals reach it, and each read is bounded (40 days or 13 months, 20,000 audit rows, 413 above the response limit)
  #checkov:skip=CKV_AWS_116:invoked synchronously (ALB, Function URL); a DLQ only applies to async invocations
  #checkov:skip=CKV_AWS_117:calls only AWS public endpoints (Bedrock, DynamoDB, AgentCore); no VPC resources to reach
  #checkov:skip=CKV_AWS_272:deployed by Terraform from this repo; code signing needs a Signer profile outside this POC
  function_name = "${var.name}-dashboard"
  role          = aws_iam_role.dashboard.arn
  tracing_config {
    mode = "Active"
  }
  kms_key_arn      = aws_kms_key.main.arn
  runtime          = "python3.13"
  handler          = "dashboard.lambda_handler"
  architectures    = ["arm64"]
  memory_size      = 512
  timeout          = 30
  filename         = data.archive_file.dashboard.output_path
  source_code_hash = data.archive_file.dashboard.output_base64sha256
  layers           = [aws_lambda_layer_version.boto3.arn] # the runtime's boto3 lacks the gateway rate-limit API (layer.tf)
  environment {
    variables = merge({
      TABLE                = aws_dynamodb_table.usage.name
      SK_INDEX             = "by_sk"
      GROUP_MODELS         = jsonencode(var.group_models)
      MODEL_CATALOG        = jsonencode(local.model_catalog)
      MODEL_ALIASES        = jsonencode(local.model_aliases)
      TAG_DIMENSIONS       = join(",", var.tag_dimensions)
      PRICES               = jsonencode(local.model_prices)
      DIRECTORY_FROM_TABLE = "1"
      GATEWAY_ID           = aws_bedrockagentcore_gateway.gw.gateway_id
      DEFAULT_BUDGET_USD   = local.default_limits_env.DEFAULT_BUDGET_USD
      DEFAULT_DAILY_USD    = local.default_limits_env.DEFAULT_DAILY_USD
      DEFAULT_HOURLY_USD   = local.default_limits_env.DEFAULT_HOURLY_USD
      DEFAULT_RPM          = local.default_limits_env.DEFAULT_RPM
      PRIVATE_ROLE         = var.dashboard_private_role
      }, local.dashboard_alb ? {
      # Behind the ALB: accept only requests carrying an x-amzn-oidc-data header signed by this ALB, from a member
      # of an admin or viewer group (dashboard.py). Set only when the ALB exists.
      ALB_ARN       = aws_lb.dashboard[0].arn
      ADMIN_GROUPS  = join(",", var.dashboard_admin_groups)
      VIEWER_GROUPS = join(",", var.dashboard_viewer_groups)
      # The same groups claim the gateway reads (Okta: groups, JumpCloud: the group attribute set on the app, groups in docs/jumpcloud.md, Cognito: cognito:groups).
      GROUP_CLAIM = var.dashboard_group_claim != "" ? var.dashboard_group_claim : var.group_claim
    } : {})
  }
  depends_on = [aws_cloudwatch_log_group.dashboard, aws_iam_role_policy.dashboard_logs, aws_dynamodb_table_item.directory]
}

resource "aws_lambda_function_url" "dashboard" {
  count              = local.dashboard_alb ? 0 : 1
  function_name      = aws_lambda_function.dashboard.function_name
  authorization_type = "AWS_IAM"
}

output "dashboard_url" { value = local.dashboard_alb ? "https://${var.dashboard_hostname}/" : aws_lambda_function_url.dashboard[0].function_url }
