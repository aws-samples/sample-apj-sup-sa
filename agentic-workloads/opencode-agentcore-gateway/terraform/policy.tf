# Per-user controls: a Mantle project per user (usage attribution), the REQUEST interceptor (identity, model
# allow-list, budget gate), the usage sync, and gateway rate limits (RPM + TPM per user).

locals {
  # Paths go into `sh -c` strings, so each is single-quoted (a ' inside written as '\''): a checkout under a folder
  # with a space or other shell character must still work. var.python is a whole command (it may hold arguments).
  # Paths are relative to this folder (Terraform runs commands from it), never absolute: destroy steps run the
  # command stored in state, so an absolute path would break a removal applied from another checkout or machine.
  # The interpreter is chosen when each command runs, by helpers/python.sh (the repo's .venv, else python3, each only
  # with the helpers' requirements, else uv with them), for the same reason. var.python, when set, is the operator's own command.
  python = var.python != "" ? var.python : "sh '${replace("${path.module}/helpers/python.sh", "'", "'\\''")}'"
  helper = "${local.python} '${replace("${path.module}/helpers/acgw.py", "'", "'\\''")}'"
  env    = merge({ AWS_REGION = var.region }, var.aws_profile == null ? {} : { AWS_PROFILE = var.aws_profile })
  envsh  = "${var.aws_profile == null ? "" : "AWS_PROFILE='${replace(var.aws_profile, "'", "'\\''")}' "}AWS_REGION='${replace(var.region, "'", "'\\''")}'"
}

# ---------------- Mantle projects (helper: no Terraform resource yet) ----------------

# One data source for every user: one helper process lists the account's projects once and creates the missing ones,
# so no cache or lock is needed across parallel processes. The users travel in the query (JSON on stdin), never in
# the shell string, so a quote in a user or group name cannot break or extend the command.
locals {
  project_tags = { for u, g in var.users : u => {
    Project = "opencode-agentcore-gateway", Component = "mantle-project", User = u, Group = g
  } }
}

data "external" "projects" {
  program = ["sh", "-c", "${local.envsh} ${local.helper} projects-ensure"]
  query   = { users = jsonencode({ for u, t in local.project_tags : "${var.name}-${u}" => t }) }
}

# A user moved to another group: an existing project's tags follow at apply time (tags are only set on create
# otherwise), next to the ledger's group (ledger.tf seed_group). One step for every project: one helper process,
# one listing (it carries the tags), and a write only for a project whose Terraform-set tags differ. It runs again
# whenever a user's tags or project id change. A new user's project is created with its tags at plan time, by the
# data source above (a documented limit: README, terraform plan creates Mantle projects).
locals {
  project_tags_by_id = { for u, t in local.project_tags : local.project_ids[u] => t }
}

resource "terraform_data" "project_tags" {
  triggers_replace = [jsonencode(local.project_tags_by_id)]
  provisioner "local-exec" {
    # One environment variable per project (OCGW_TAGS_<n> = {"<id>": tags}), never inside the shell string: Linux
    # refuses to start a process with any single variable over 128 KiB, which one map for every user would pass.
    command = "${local.helper} projects-retag"
    # OCGW_TAGS_N: how many there are, so variables left in the operator's shell are never read.
    environment = merge(local.env, { OCGW_TAGS_N = tostring(length(local.project_tags_by_id)) }, { for i, id in sort(keys(local.project_tags_by_id)) :
    "OCGW_TAGS_${i}" => jsonencode({ (id) = local.project_tags_by_id[id] }) })
  }
}

resource "terraform_data" "project_archive" {
  for_each = var.users
  # No triggers_replace here: replacing this resource runs its destroy step, which archives the user's live project
  # (r93 tried one keyed on the name, and the upgrade archived every user's project). A changed var.name therefore
  # leaves the old <name>-<user> projects live: archive them in the Bedrock console (README, Limits).
  # user: the destroy step archives only projects tagged User=<user> (r196: another stack's same-named project, such
  # as ocgw-dev-alice of stack "ocgw-dev" next to user "dev-alice" of stack "ocgw", stays live). Adding it to input is
  # an in-place change; a state written before it has no user, and the helper then matches on the name alone.
  input = { name = "${var.name}-${each.key}", user = each.key, cmd = local.helper, env = local.env }
  provisioner "local-exec" {
    when = destroy
    # The name travels in an environment variable (acgw.py reads @env:NAME), never inside the shell string.
    command     = "${self.input.cmd} project-archive @env:OCGW_PROJECT"
    environment = merge(self.input.env, { OCGW_PROJECT = self.input.name, OCGW_USER = lookup(self.input, "user", "") })
  }
}

locals {
  project_ids = { for u in keys(var.users) : u => jsondecode(data.external.projects.result["${var.name}-${u}"]).id }
}

# ---------------- REQUEST interceptor ----------------

resource "aws_iam_role" "interceptor" {
  name = "${var.name}-interceptor"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

# Logs to this function's own log group only (Terraform creates it), not every group in the account.
resource "aws_iam_role_policy" "interceptor_logs" {
  role = aws_iam_role.interceptor.id
  name = "own-logs"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
    Resource = "${aws_cloudwatch_log_group.interceptor.arn}:*" }]
  })
}

resource "aws_iam_role_policy" "interceptor" {
  role = aws_iam_role.interceptor.id
  name = "ledger-read"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["dynamodb:BatchGetItem", "dynamodb:GetItem", "dynamodb:Query"], Resource = aws_dynamodb_table.usage.arn },
      # the per-user requests-per-minute counter (USER#<u> / RPM#<minute> items, 10-minute ttl) and the token's groups
      # on USER#<u> / BUDGET, and the web search charge (spend_usd, websearch_requests) on USER#<u> window items; this role parses untrusted input, so it may update only those attributes of USER# items.
      { Effect = "Allow", Action = ["dynamodb:UpdateItem"], Resource = aws_dynamodb_table.usage.arn,
        Condition = {
          "ForAllValues:StringLike"   = { "dynamodb:LeadingKeys" = ["USER#*"] }
          "ForAllValues:StringEquals" = { "dynamodb:Attributes" = concat(["pk", "sk", "requests", "ttl", "token_groups", "groups_iat", "groups_seen_at", "spend_usd", "websearch_requests", "last_seen_at"], [for k in keys(var.openweight_models) : "m|ow/${k}|req"]) }
    } }]
  })
}

data "archive_file" "interceptor" {
  type        = "zip"
  output_path = "${path.module}/build/interceptor.zip"
  source {
    content  = file("${path.module}/../lambda/interceptor.py")
    filename = "interceptor.py"
  }
  source {
    content  = file("${path.module}/../lambda/policy.py")
    filename = "policy.py"
  }
}

resource "aws_cloudwatch_log_group" "interceptor" {
  name              = "/aws/lambda/${var.name}-interceptor"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
  tags              = { Component = "interceptor" }
}

resource "aws_lambda_function" "interceptor" {
  #checkov:skip=CKV_AWS_115:reserved concurrency would carve capacity out of the account pool; only the two gateway roles invoke it, and a request refused by the gateway's JWT authorizer never reaches it
  #checkov:skip=CKV_AWS_116:invoked synchronously by the two gateways only; a DLQ only applies to async invocations
  #checkov:skip=CKV_AWS_117:calls only AWS public endpoints (Bedrock, DynamoDB, AgentCore); no VPC resources to reach
  #checkov:skip=CKV_AWS_272:deployed by Terraform from this repo; code signing needs a Signer profile outside this POC
  function_name = "${var.name}-interceptor"
  role          = aws_iam_role.interceptor.arn
  tracing_config {
    mode = "Active"
  }
  kms_key_arn      = aws_kms_key.main.arn
  runtime          = "python3.13"
  handler          = "interceptor.lambda_handler"
  architectures    = ["arm64"]
  memory_size      = 512
  timeout          = 30
  filename         = data.archive_file.interceptor.output_path
  source_code_hash = data.archive_file.interceptor.output_base64sha256
  environment {
    variables = {
      USER_CLAIM            = var.user_claim
      DIRECTORY_FROM_TABLE  = "1"                      # PROJECTS and IDENTITY_MAP live in the ledger's CONFIG/DIRECTORY item (ledger.tf)
      IDENTITY_MAP_REQUIRED = local.cognito ? "" : "1" # okta, jumpcloud: only identity_map names a user, even if it arrives empty
      GROUP_CLAIM           = var.group_claim
      GROUP_MODELS          = jsonencode(var.group_models)
      TABLE                 = aws_dynamodb_table.usage.name
      DEFAULT_BUDGET_USD    = local.default_limits_env.DEFAULT_BUDGET_USD
      DEFAULT_DAILY_USD     = local.default_limits_env.DEFAULT_DAILY_USD
      DEFAULT_HOURLY_USD    = local.default_limits_env.DEFAULT_HOURLY_USD
      DEFAULT_RPM           = local.default_limits_env.DEFAULT_RPM
      FALLBACKS             = jsonencode({ for k, r in var.fallback_rules : "${r.target}/${r.model}" => [for f in r.fallback : "${f.target}/${f.model}"] })
      MANTLE_IDS            = jsonencode({ for k, m in var.openweight_models : "ow/${k}" => m.id })
      MAX_OUT               = jsonencode({ for k, m in var.openweight_models : "ow/${k}" => m.maxOut })
      MODEL_ALIASES         = jsonencode(local.model_aliases)
      MODEL_CATALOG         = jsonencode(local.model_catalog)
      WEBSEARCH_MCP_URL     = aws_bedrockagentcore_gateway.websearch.gateway_url
    }
  }
  tags       = { Component = "interceptor" }
  depends_on = [aws_cloudwatch_log_group.interceptor, aws_iam_role_policy.interceptor_logs, aws_dynamodb_table_item.directory]
}

# ---------------- Usage sync (every 5 minutes): Mantle per-project token metrics -> ledger ----------------

resource "aws_iam_role" "usage_sync" {
  name = "${var.name}-usage-sync"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

# Logs to this function's own log group only (Terraform creates it), not every group in the account.
resource "aws_iam_role_policy" "usage_sync_logs" {
  role = aws_iam_role.usage_sync.id
  name = "own-logs"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
    Resource = "${aws_cloudwatch_log_group.usage_sync.arn}:*" }]
  })
}

resource "aws_iam_role_policy" "usage_sync" {
  role = aws_iam_role.usage_sync.id
  name = "metrics-ledger"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Read-only, and "*" is the narrowest IAM allows: neither action has a resource type or a namespace condition
      # (cloudwatch:namespace applies to PutMetricData only).
      { Effect = "Allow", Action = ["cloudwatch:ListMetrics", "cloudwatch:GetMetricData"], Resource = "*" },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Scan"], Resource = aws_dynamodb_table.usage.arn },
      # Updates only the users' window items and the rollup index items: never CONFIG/DIRECTORY (who a caller is) or
      # POLICY, which a bug or compromised dependency here could otherwise rewrite. On USER# items only the model
      # spend attributes it owns (set_rollup), so it cannot change a budget cap, boost, rpm or token groups on BUDGET.
      { Effect = "Allow", Action = ["dynamodb:UpdateItem"], Resource = aws_dynamodb_table.usage.arn,
        Condition = {
          "ForAllValues:StringLike" = {
            "dynamodb:LeadingKeys" = ["USER#*"]
            "dynamodb:Attributes" = ["pk", "sk", "ttl", "model_spend_usd", "model_input_tokens", "model_output_tokens",
            "model_synced_at", "model_by_model", "model_base", "model_project", "m|*|usd", "m|*|in", "m|*|out"]
          }
      } },
      { Effect = "Allow", Action = ["dynamodb:UpdateItem"], Resource = aws_dynamodb_table.usage.arn,
        Condition = {
          "ForAllValues:StringEquals" = {
            "dynamodb:LeadingKeys" = ["ROLLUPINDEX", "MODELINDEX", "TAGINDEX"]
            "dynamodb:Attributes"  = ["pk", "sk", "keys", "updated_at", "ttl"]
          }
      } },
      { Effect = "Allow", Action = ["dynamodb:Query"], Resource = "${aws_dynamodb_table.usage.arn}/index/by_sk" },
      # Derived group, model and tag rollups are written whole (PutItem) and deleted when nobody is left in a window;
      # no other partition can be put or deleted.
      { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:DeleteItem"], Resource = aws_dynamodb_table.usage.arn,
      Condition = { "ForAllValues:StringLike" = { "dynamodb:LeadingKeys" = ["GROUP#*", "MODEL#*", "TAG#*"] } } },
    ]
  })
}

data "archive_file" "usage_sync" {
  type        = "zip"
  output_path = "${path.module}/build/usage_sync.zip"
  source {
    content  = file("${path.module}/../lambda/usage_sync.py")
    filename = "usage_sync.py"
  }
  source {
    content  = file("${path.module}/../lambda/policy.py") # budget_groups: the groups rule the dashboard uses too
    filename = "policy.py"
  }
}

resource "aws_cloudwatch_log_group" "usage_sync" {
  name              = "/aws/lambda/${var.name}-usage-sync"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
  tags              = { Component = "usage-sync" }
}

resource "aws_lambda_function" "usage_sync" {
  #checkov:skip=CKV_AWS_115:one scheduled invocation every 5 minutes
  #checkov:skip=CKV_AWS_116:invoked asynchronously by its EventBridge schedule; a failed run is followed by the next run 5 minutes later, which writes the same windows again, so a DLQ adds nothing
  #checkov:skip=CKV_AWS_117:calls only AWS public endpoints (Bedrock, DynamoDB, AgentCore); no VPC resources to reach
  #checkov:skip=CKV_AWS_272:deployed by Terraform from this repo; code signing needs a Signer profile outside this POC
  function_name = "${var.name}-usage-sync"
  role          = aws_iam_role.usage_sync.arn
  tracing_config {
    mode = "Active"
  }
  kms_key_arn      = aws_kms_key.main.arn
  runtime          = "python3.13"
  handler          = "usage_sync.lambda_handler"
  architectures    = ["arm64"]
  memory_size      = 256
  timeout          = 60
  filename         = data.archive_file.usage_sync.output_path
  source_code_hash = data.archive_file.usage_sync.output_base64sha256
  environment {
    variables = {
      TABLE                = aws_dynamodb_table.usage.name
      SK_INDEX             = "by_sk"
      DIRECTORY_FROM_TABLE = "1"
      PRICES               = jsonencode({ for k, m in var.openweight_models : m.id => merge({ alias = "ow/${k}", in = m.in, out = m.out }, m.cache_write == null ? {} : { cache_write = m.cache_write }) })
      TAG_DIMENSIONS       = join(",", var.tag_dimensions)
    }
  }
  tags       = { Component = "usage-sync" }
  depends_on = [aws_cloudwatch_log_group.usage_sync, aws_iam_role_policy.usage_sync_logs, aws_dynamodb_table_item.directory]
}

resource "aws_cloudwatch_event_rule" "usage_sync" {
  name                = "${var.name}-usage-sync"
  schedule_expression = "rate(5 minutes)"
  tags                = { Component = "usage-sync" }
}

resource "aws_cloudwatch_event_target" "usage_sync" {
  rule = aws_cloudwatch_event_rule.usage_sync.name
  arn  = aws_lambda_function.usage_sync.arn
}

resource "aws_lambda_permission" "usage_sync" {
  statement_id  = "EventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.usage_sync.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.usage_sync.arn
}

# ---------------- Rate limits (helper: not in the provider as of v6.69.1) ----------------

locals {
  rl_key = "$.context.jwt.${var.user_claim}"
  # Rate-limit entries match the raw claim value, so map each ledger user to all of its IdP ids (a user may have one
  # per IdP); without identity_map (cognito) the claim value is the ledger name.
  # With an identity_map the claim is an IdP id, so a user with none mapped gets no entry: one keyed on the ledger name
  # would apply to whichever IdP user happens to carry that claim value.
  rl_ids = length(var.identity_map) == 0 ? { for u in keys(var.users) : u => [u] } : merge(
  { for u in keys(var.users) : u => [] }, { for u, ids in { for id, u in var.identity_map : u => id... } : u => sort(ids) })
  rate_limit_body = {
    dimensionKeys = [local.rl_key]
    entries = concat(
      # An override entry replaces "*" for that user, so it repeats the tokens-per-minute cap; without it the user
      # would have no TPM limit at all.
      flatten([for u, rpm in var.rpm_overrides : [for id in local.rl_ids[u] : { dimensions = { (local.rl_key) = id }, requests = [{ rate = rpm, period = "minute" }], tokens = [{ rate = var.tpm_default, period = "minute" }] }]]),
      [{ dimensions = { (local.rl_key) = "*" }, requests = [{ rate = var.rpm_default, period = "minute" }], tokens = [{ rate = var.tpm_default, period = "minute" }] }],
    )
  }
  # The gateway allows one limit per set of dimension keys, so a new version updates the limit in place
  # (helpers/acgw.py ratelimit-ensure) and sets a description carrying a hash of the body and the gateway id; the old
  # version's destroy step deletes only a limit still carrying its own description, so the limit is never removed
  # by a spec change. The gateway is never left without its "*" limit.
  rate_limit = merge(local.rate_limit_body, {
    description = "${var.name} requests and tokens per minute per user #${substr(sha256(jsonencode([aws_bedrockagentcore_gateway.gw.gateway_id, local.rate_limit_body])), 0, 8)}"
  })
}

resource "terraform_data" "rate_limit" {
  # A changed spec replaces this resource: create the new limit first, then delete the old one by its own description.
  # The description's hash covers the gateway id too, so a replaced gateway also replaces this resource and gets its
  # "*" limit (an in-place input change would not run the create provisioner), and the old one's destroy step, aimed
  # at the old gateway, never deletes the new limit.
  triggers_replace = jsonencode(local.rate_limit)
  lifecycle {
    create_before_destroy = true
  }
  input = { gw = aws_bedrockagentcore_gateway.gw.gateway_id, spec = jsonencode(local.rate_limit), desc = local.rate_limit.description, cmd = local.helper, env = local.env }
  provisioner "local-exec" {
    # The spec travels in an environment variable (read by acgw.py as @env:NAME), not inside a quoted shell argument.
    command = "${self.input.cmd} ratelimit-ensure ${self.input.gw} @env:OCGW_RL_SPEC"
    # OCGW_RL_OWNER: this instance's id, tagged on the limit, so only the instance that set it last deletes it.
    environment = merge(self.input.env, { OCGW_RL_SPEC = self.input.spec, OCGW_RL_OWNER = self.id })
  }
  provisioner "local-exec" {
    when        = destroy
    command     = "${self.input.cmd} ratelimit-delete ${self.input.gw} @env:OCGW_RL_DESC"
    environment = merge(self.input.env, { OCGW_RL_DESC = self.input.desc, OCGW_RL_OWNER = self.id })
  }
  depends_on = [aws_bedrockagentcore_gateway_target.ow]
}

output "projects" { value = local.project_ids }
output "interceptor_log_group" { value = aws_cloudwatch_log_group.interceptor.name }
output "usage_sync_function" { value = aws_lambda_function.usage_sync.function_name }
