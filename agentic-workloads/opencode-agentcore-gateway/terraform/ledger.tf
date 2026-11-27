# Usage ledger (DynamoDB): per-user, per-window spend and token counters, budgets, audit log and rate-limit counters.

resource "aws_dynamodb_table" "usage" {
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.main.arn
  }
  name = "${var.name}-usage"
  # The ledger holds the audit trail; turn this off (and apply) before terraform destroy.
  deletion_protection_enabled = var.ledger_deletion_protection
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
  # Rollup reads by period (every MONTH#/DAY#/HOUR#/BUDGET row of one period) are a Query here instead of a Scan of
  # the whole table, which also holds every other user's, group's, model's and tag's windows, RPM# counters and audit rows.
  # The index keys on sk, which every item has, so RPM# counters and audit rows are copied too (about one extra write
  # unit per request). Kept on purpose: a sparse index needs a new attribute written on every rollup row by the
  # interceptor, usage sync and dashboard, plus a back-fill of existing rows, or the dashboard would miss the current
  # month until each row is written again.
  global_secondary_index {
    name            = "by_sk"
    hash_key        = "sk"
    range_key       = "pk"
    projection_type = "ALL"
  }
  tags = { Component = "ledger" }
}

# Cost-attribution tags per user (usage_sync reads USER#<u>/PROFILE). Terraform owns these items.
resource "aws_dynamodb_table_item" "profile" {
  for_each   = var.user_tags
  table_name = aws_dynamodb_table.usage.name
  hash_key   = "pk"
  range_key  = "sk"
  item = jsonencode({
    pk   = { S = "USER#${each.key}" }
    sk   = { S = "PROFILE" }
    tags = { M = { for k, v in each.value : k => { S = v } } }
  })
}

# Seeds each user's BUDGET item once. A user-level budget_usd outranks group and org policy, so only users listed in
# budget_overrides_usd get one; everyone else falls through to their group's policy, then the org default. After the
# seed the dashboard owns the item (limits, boosts), so Terraform ignores later drift instead of overwriting it.
# The create is a conditional PutItem: a user who already has a BUDGET item (the dashboard writes limits, or an earlier
# apply seeded it; the interceptor never creates one) fails the apply when added here. Adopt the item instead of re-creating it:
#   terraform import 'aws_dynamodb_table_item.budget["<user>"]' '<table>|pk|USER#<user>|sk|BUDGET'
# (Narrowing for_each to budget_overrides_usd would delete the live items of everyone else, so it stays on var.users.)
resource "aws_dynamodb_table_item" "budget" {
  for_each   = var.users
  table_name = aws_dynamodb_table.usage.name
  hash_key   = "pk"
  range_key  = "sk"
  item = jsonencode(merge({
    pk    = { S = "USER#${each.key}" }
    sk    = { S = "BUDGET" }
    group = { S = each.value }
  }, contains(keys(var.budget_overrides_usd), each.key) ? { budget_usd = { N = tostring(var.budget_overrides_usd[each.key]) } } : {}))
  lifecycle {
    ignore_changes = [item]
  }
}

# ignore_changes above keeps Terraform from overwriting what the dashboard and interceptor store on the item, but it
# would also keep a user's new group in var.users from ever reaching it. This writes only the group attribute, on
# create and whenever the group changes, so a user who has not called yet (no token groups) is filed under the right
# group by the dashboard and the usage sync.
resource "terraform_data" "seed_group" {
  # One step and one helper process for every user (not one each), re-run whenever any user's group changes.
  triggers_replace = [jsonencode(var.users)]
  depends_on       = [aws_dynamodb_table_item.budget]
  provisioner "local-exec" {
    # Values travel in environment variables (one per user, as for the project tags), never inside the shell string.
    command = "${local.helper} seed-groups @env:OCGW_TABLE"
    environment = merge(local.env, { OCGW_TABLE = aws_dynamodb_table.usage.name, OCGW_GROUPS_N = tostring(length(var.users)) },
    { for i, u in sort(keys(var.users)) : "OCGW_GROUPS_${i}" => jsonencode({ (u) = var.users[u] }) })
  }
}


# The per-user maps the Lambdas need (ledger user -> Mantle project id, IdP user id -> ledger user). They live here,
# not in Lambda environment variables, because Lambda caps all of a function's variables at 4 KB together, which
# these maps pass at about 40 users. The interceptor, usage sync and dashboard read the item (policy.directory) and
# cache it for a minute, so a change here reaches them within about a minute of `terraform apply`.
resource "aws_dynamodb_table_item" "directory" {
  table_name = aws_dynamodb_table.usage.name
  hash_key   = "pk"
  range_key  = "sk"
  item = jsonencode({
    pk           = { S = "CONFIG" }
    sk           = { S = "DIRECTORY" }
    projects     = { S = jsonencode(local.project_ids) }
    identity_map = { S = jsonencode(var.identity_map) }
  })
}
