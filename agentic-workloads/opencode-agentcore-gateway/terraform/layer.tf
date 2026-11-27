# boto3 for the dashboard Lambda. The Python runtime's bundled boto3 trails the AgentCore API: on the first live deploy
# GET /api/v2/ratelimits failed with "'BedrockAgentCoreControl' object has no attribute 'list_gateway_rate_limits'".
# The layer pins the version the code needs (boto3 and botocore are pure Python, so one build serves arm64).
variable "boto3_version" {
  description = "boto3 version for the dashboard Lambda layer (needs the AgentCore gateway rate-limit API, 1.43.118 or later)"
  type        = string
  default     = "1.43.118"
  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.boto3_version))
    error_message = "boto3_version must be an exact version such as 1.43.118."
  }
  validation {
    # The same minimum as helpers/agentcore_wait.py MIN_BOTO3: older versions lack list_gateway_rate_limits.
    condition     = try(tonumber(format("%05d%05d%05d", [for x in split(".", var.boto3_version) : tonumber(x)]...)) >= 10004300118, false)
    error_message = "boto3_version must be 1.43.118 or later (the dashboard needs the gateway rate-limit API)."
  }
}

variable "boto3_layer_resolved_before" {
  description = "Date (YYYY-MM-DD) the layer's dependencies are resolved as of (uv --exclude-newer), so every machine builds the same zip. Move it on with boto3_version"
  type        = string
  default     = "2026-11-13"
  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.boto3_layer_resolved_before))
    error_message = "boto3_layer_resolved_before must be a date such as 2026-11-13."
  }
}

locals {
  boto3_layer_dir = "${path.module}/build/boto3-layer"
}

# Built by a data source on every plan (not a provisioner), so the git-ignored zip is recreated wherever Terraform
# runs: a fresh clone, CI, another laptop on the same state, or after terraform/build/ was removed. The helper zips
# only when this version's zip is missing; otherwise it just reports the existing zip's hash.
data "external" "boto3_layer" {
  program = ["python3", "${path.module}/helpers/build_layer.py"]
  query   = { dir = local.boto3_layer_dir, version = var.boto3_version, resolved_before = var.boto3_layer_resolved_before } # relative: an absolute path in state replaces the layer from another checkout
}

resource "aws_lambda_layer_version" "boto3" {
  layer_name               = "${var.name}-boto3"
  description              = "boto3 ${var.boto3_version} for the dashboard (AgentCore gateway rate limits)"
  filename                 = data.external.boto3_layer.result.zip
  source_code_hash         = data.external.boto3_layer.result.sha256
  compatible_runtimes      = ["python3.13"]
  compatible_architectures = ["arm64"]
}
