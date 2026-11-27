data "aws_caller_identity" "me" {}

locals {
  acct = data.aws_caller_identity.me.account_id
}

resource "aws_iam_role" "gateway" {
  name = "${var.name}-gateway"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRole"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.acct }
        ArnLike      = { "aws:SourceArn" = "arn:aws:bedrock-agentcore:${var.region}:${local.acct}:gateway/${var.name}-gw-*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "gateway" {
  role = aws_iam_role.gateway.id
  name = "mantle-and-interceptor"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Inference only, on this account's Mantle projects (one per user), as in the AWS managed policy
      # AmazonBedrockMantleInferenceAccess; project administration stays with Terraform's own credentials.
      { Sid = "MantleInference", Effect = "Allow", Action = ["bedrock-mantle:CreateInference", "bedrock-mantle:Get*", "bedrock-mantle:List*"],
      Resource = "arn:aws:bedrock-mantle:${var.region}:${local.acct}:project/*" },
      # First use of a third-party model subscribes through AWS Marketplace, only when Mantle makes the call.
      { Sid = "MarketplaceViaMantle", Effect = "Allow", Action = ["aws-marketplace:Subscribe", "aws-marketplace:ViewSubscriptions"],
      Resource = "*", Condition = { StringEquals = { "aws:CalledViaLast" = "bedrock-mantle.amazonaws.com" } } },
      # Only the interceptors: a ${var.name}-* wildcard would also let the gateway invoke the dashboard and usage-sync Lambdas.
      { Sid = "Interceptors", Effect = "Allow", Action = "lambda:InvokeFunction", Resource = [local.interceptor_arn] },
    ]
  })
}

resource "aws_bedrockagentcore_gateway" "gw" {
  name            = "${var.name}-gw"
  description     = "opencode gateway: OpenAI-compatible /inference/v1 -> open-weight models on Amazon Bedrock (Mantle)"
  role_arn        = aws_iam_role.gateway.arn
  authorizer_type = "CUSTOM_JWT"
  exception_level = var.gateway_debug_errors ? "DEBUG" : null
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url    = local.discovery_url
      allowed_clients  = length(local.allowed_clients) > 0 ? local.allowed_clients : null
      allowed_audience = length(var.oidc_allowed_audience) > 0 ? var.oidc_allowed_audience : null
    }
  }
  interceptor_configuration {
    interception_points = ["REQUEST"]
    interceptor {
      lambda { arn = aws_lambda_function.interceptor.arn }
    }
    input_configuration { pass_request_headers = true }
  }
  depends_on = [terraform_data.role_propagation]
}

# Target `ow`: built-in bedrock-mantle connector. opencode sends OpenAI Chat Completions (or Responses) to
# /inference/v1; the connector serves open-weight models from Mantle with the gateway IAM role.
resource "aws_bedrockagentcore_gateway_target" "ow" {
  gateway_identifier = aws_bedrockagentcore_gateway.gw.gateway_id
  name               = "ow"
  description        = "Open-weight models on Bedrock Mantle (Chat Completions, Responses), gateway IAM role"
  target_configuration {
    inference {
      connector {
        source { connector_id = "bedrock-mantle" }
      }
    }
  }
  credential_provider_configuration {
    gateway_iam_role {}
  }
  depends_on = [terraform_data.role_propagation]
}

# New IAM roles take a few seconds before AgentCore can assume them; target creation discovers
# models with the role straight away and fails without this pause.
resource "terraform_data" "role_propagation" {
  triggers_replace = [aws_iam_role.gateway.arn, aws_iam_role_policy.gateway.policy]
  provisioner "local-exec" { command = "sleep 30" }
}
