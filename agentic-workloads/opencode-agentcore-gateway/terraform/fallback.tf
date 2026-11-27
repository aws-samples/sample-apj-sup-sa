# Model fallback on the gateway (AgentCore Gateway feature of 22 Oct 2026) and the gateway's application logs,
# whose routing records name the hop that answered (servedViaFallbackHop:N).
#
# aws_bedrockagentcore_gateway_rule (provider 6.69.1) has no matchModels / model / fallback fields yet, so each rule is a
# terraform_data custom resource that runs helpers/gateway_rule.py (boto3 >= 1.43.118) on create, change and destroy.
# Every hop is the `ow` inference target. Usage is metered per user from the Mantle project the call ran under, so the
# ledger charges the model that answered.

variable "fallback_rules" {
  description = "Fallback chains, keyed by name. match lists the model strings as clients send them (with and without the target prefix)."
  type = map(object({
    priority              = number
    match                 = list(string)
    target                = string
    model                 = string
    fallback              = list(object({ target = string, model = string }))
    status_codes          = optional(list(string), ["5XX", "429"]) # 429 is not a default trigger
    errors                = optional(list(string), ["CONNECTION_ERROR", "FIRST_BYTE_TIMEOUT", "UNROUTABLE"])
    first_byte_timeout_ms = optional(number, 60000) # no default timeout otherwise; reasoning models can be slow to start
  }))
  default = {
    "glm-5.3" = {
      priority = 100
      match    = ["ow/glm-5.3", "glm-5.3"]
      target   = "ow"
      model    = "glm-5.3"
      fallback = [{ target = "ow", model = "kimi-k3" }]
    }
  }
  validation {
    # The gateway routes every name in match to model, but the interceptor checks the allow-list and the fallback
    # chain for the name the client sent: a match naming another model would serve a model the caller was not allowed.
    condition     = alltrue([for r in values(var.fallback_rules) : alltrue([for m in r.match : contains(["ow/${r.model}", r.model], m)])])
    error_message = "fallback_rules match may list only the rule's own model, as ow/<model> or <model> (short names from model_aliases are added automatically)."
  }
  validation {
    condition     = alltrue(flatten([for r in values(var.fallback_rules) : [for t in concat([r.target], [for f in r.fallback : f.target]) : t == "ow"]]))
    error_message = "fallback_rules targets and hops must be ow (the only inference target)."
  }
  validation {
    condition = alltrue(flatten([for r in values(var.fallback_rules) : [for m in concat([r.model], [for f in r.fallback : f.model]) :
    contains(keys(var.openweight_models), m)]]))
    error_message = "fallback_rules model and hop models must be openweight_models aliases (for example kimi-k3); Terraform turns them into Mantle ids."
  }
  # The gateway serves a fallback hop after the interceptor has checked the requested model, so a hop must never reach a
  # model the caller is not allowed: every group allowed the primary model must also be allowed every hop's model.
  # "Allowed" is policy.model_allowed's rule: an exact ID or a "prefix*" pattern.
  validation {
    condition = alltrue(flatten([for r in values(var.fallback_rules) : [for g, ms in var.group_models :
      [for f in r.fallback : anytrue([for p in ms : p == "ow/${f.model}" || (endswith(p, "*") && startswith("ow/${f.model}", trimsuffix(p, "*")))])]
    if anytrue([for p in ms : p == "ow/${r.model}" || (endswith(p, "*") && startswith("ow/${r.model}", trimsuffix(p, "*")))])]]))
    error_message = "Every group that may use a fallback rule's model must also be allowed each fallback model (group_models)."
  }
}

variable "rule_python" {
  description = "Command that runs Python with boto3 >= 1.43.118 for the gateway-rule helper. Empty = the same choice as var.python"
  type        = string
  default     = ""
}

# The `ow` target is the bedrock-mantle connector, which knows Mantle model ids (moonshotai.kimi-k3), not the ow/
# aliases. The interceptor rewrites the request to the Mantle id, so the rule matches that id as well as every name a
# client may send (in case matching runs on the original body), and each hop names the Mantle id it should call.
locals {
  mantle_id = { for k, m in var.openweight_models : k => m.id }
  fallback_match = { for k, r in var.fallback_rules : k => distinct(concat(r.match, [local.mantle_id[r.model]],
  [for a, m in local.model_aliases : a if m == "${r.target}/${r.model}"])) }
  fallback_spec = { for k, r in var.fallback_rules : k => merge(r, {
    model    = local.mantle_id[r.model]
    fallback = [for f in r.fallback : { target = f.target, model = local.mantle_id[f.model] }]
  }) }
}

resource "terraform_data" "fallback_rule" {
  for_each = var.fallback_rules
  input = {
    python = var.rule_python != "" ? var.rule_python : local.python # one interpreter choice for every helper
    script = "${path.module}/helpers/gateway_rule.py"               # relative: the destroy step runs the path stored in state
    spec = jsonencode(merge(local.fallback_spec[each.key], {
      match       = local.fallback_match[each.key]
      region      = var.region
      profile     = var.aws_profile
      gateway     = aws_bedrockagentcore_gateway.gw.gateway_id
      description = "${var.name} fallback ${each.key}"
    }))
  }
  # Any change to the rule replaces this resource. create_before_destroy runs the new instance's apply first, which
  # updates the existing rule in place (UpdateGatewayRule) and renames it to "<description> #<spec hash>", so fallback
  # never goes off. The old instance's destroy step deletes only a rule still carrying its own (old) hash, so it leaves
  # the replacement's rule alone even when the rule body did not change.
  triggers_replace = [aws_bedrockagentcore_gateway.gw.gateway_id, local.fallback_spec[each.key], local.fallback_match[each.key], var.name]
  lifecycle {
    create_before_destroy = true
  }

  provisioner "local-exec" {
    command     = "${self.input.python} '${replace(self.input.script, "'", "'\\''")}' apply"
    environment = { RULE_SPEC = self.input.spec, RULE_OWNER = self.id }
  }
  provisioner "local-exec" {
    when        = destroy
    command     = "${self.input.python} '${replace(self.input.script, "'", "'\\''")}' delete"
    environment = { RULE_SPEC = self.input.spec, RULE_OWNER = self.id }
  }
  depends_on = [aws_bedrockagentcore_gateway_target.ow]
}

# Gateway application logs (vended logs) -> CloudWatch Logs.
resource "aws_cloudwatch_log_group" "gateway_app" {
  name              = "/aws/vendedlogs/bedrock-agentcore/gateway/${aws_bedrockagentcore_gateway.gw.gateway_id}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

resource "aws_cloudwatch_log_delivery_source" "gateway_app" {
  name         = "${var.name}-gw-logs-src"
  log_type     = "APPLICATION_LOGS"
  resource_arn = aws_bedrockagentcore_gateway.gw.gateway_arn
}

resource "aws_cloudwatch_log_delivery_destination" "gateway_app" {
  name = "${var.name}-gw-logs-dst"
  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.gateway_app.arn
  }
}

resource "aws_cloudwatch_log_delivery" "gateway_app" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.gateway_app.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.gateway_app.arn
}

output "gateway_app_log_group" {
  value = aws_cloudwatch_log_group.gateway_app.name
}
