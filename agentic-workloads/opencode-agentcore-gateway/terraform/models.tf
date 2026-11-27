# The model list the REQUEST interceptor serves on GET /inference/v1/models (each caller sees only the models its
# groups allow) and the aliases it accepts. opencode lists models from its opencode.json provider block; the managed
# config this stack writes (client/opencode.json.tftpl) is generated from the same catalog.

variable "model_display_names" {
  description = "Picker label per model ID (ow/<alias>); unset = the ID"
  type        = map(string)
  default = {
    "ow/kimi-k3"       = "Kimi K3"
    "ow/glm-5.3"       = "GLM 5.3"
    "ow/deepseek-v3.2" = "DeepSeek V3.2"
    "ow/glm-5"         = "GLM 5"
  }
}

# Optional extra names a client may send. The interceptor rewrites an alias to its model before the allow-list,
# budget and routing, so logs and the ledger always show the real model.
variable "model_aliases" {
  description = "Alias model ID -> real model ID (ow/<alias>)"
  type        = map(string)
  default     = {}
  validation {
    condition     = alltrue([for a, m in var.model_aliases : !startswith(a, "ow/") && startswith(m, "ow/")])
    error_message = "An alias must not start with ow/, and must point at an ow/<alias> model."
  }
}

locals {
  model_aliases = var.model_aliases
  model_catalog = [for k, m in var.openweight_models : {
    id           = "ow/${k}"
    display_name = lookup(var.model_display_names, "ow/${k}", "ow/${k}")
    description  = "Open-weight model on Amazon Bedrock (${m.id})"
  }]
}
