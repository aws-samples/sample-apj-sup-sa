# Web search for gateway users: a second AgentCore Gateway, protocol MCP, with the AWS-served Web Search
# connector as its only target. Same inbound IdP token check as the inference gateway, so the token the ocgw client
# already holds works here too. opencode reaches it as a remote MCP server through the local ocgw proxy (/mcp).

locals {
  interceptor_arn = "arn:aws:lambda:${var.region}:${local.acct}:function:${var.name}-interceptor"
}

resource "aws_iam_role" "websearch" {
  name = "${var.name}-websearch"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRole"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.acct }
        ArnLike      = { "aws:SourceArn" = "arn:aws:bedrock-agentcore:${var.region}:${local.acct}:gateway/${var.name}-web-*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "websearch" {
  role = aws_iam_role.websearch.id
  name = "web-search"
  policy = jsonencode({
    Version = "2012-10-17"
    # The AWS-served Web Search connector of Amazon Bedrock AgentCore.
    Statement = [
      { Effect = "Allow", Action = ["bedrock-agentcore:InvokeWebSearch"], Resource = "arn:aws:bedrock-agentcore:${var.region}:aws:tool/web-search.v1" },
      # Only this gateway: AgentCore gateway IDs are "<name>-<10 random characters>", and the ARN is known only after
      # create, so the prefix is the narrowest form that exists before the gateway does.
      { Effect = "Allow", Action = ["bedrock-agentcore:GetGateway"], Resource = "arn:aws:bedrock-agentcore:${var.region}:${local.acct}:gateway/${var.name}-web-*" },
      { Effect = "Allow", Action = ["bedrock-agentcore:GetConfigurationBundleVersion"], Resource = "arn:aws:bedrock-agentcore:${var.region}:${local.acct}:configuration-bundle/*" },
      { Sid = "Interceptor", Effect = "Allow", Action = "lambda:InvokeFunction", Resource = local.interceptor_arn },
    ]
  })
}

resource "terraform_data" "websearch_role_propagation" {
  triggers_replace = [aws_iam_role.websearch.arn, aws_iam_role_policy.websearch.policy]
  provisioner "local-exec" { command = "sleep 30" }
}

resource "aws_bedrockagentcore_gateway" "websearch" {
  name            = "${var.name}-web"
  description     = "opencode gateway: AgentCore Web Search as an MCP tool for opencode"
  role_arn        = aws_iam_role.websearch.arn
  protocol_type   = "MCP"
  authorizer_type = "CUSTOM_JWT"
  exception_level = var.gateway_debug_errors ? "DEBUG" : null
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url    = local.discovery_url
      allowed_clients  = length(local.allowed_clients) > 0 ? local.allowed_clients : null
      allowed_audience = length(var.oidc_allowed_audience) > 0 ? var.oidc_allowed_audience : null
    }
  }
  # The same REQUEST interceptor as the inference gateway: a tools/call counts against the caller's rpm and is refused
  # over budget, so direct MCP use is gated like model calls.
  interceptor_configuration {
    interception_points = ["REQUEST"]
    interceptor {
      # Built from the name: the interceptor's environment reads this gateway's URL, so a reference would be a cycle.
      lambda { arn = local.interceptor_arn }
    }
    input_configuration { pass_request_headers = true }
  }
  depends_on = [terraform_data.websearch_role_propagation]
}

resource "aws_bedrockagentcore_gateway_target" "websearch" {
  gateway_identifier = aws_bedrockagentcore_gateway.websearch.gateway_id
  name               = "web"
  description        = "AgentCore Web Search connector (queries served inside AWS)"
  target_configuration {
    mcp {
      connector {
        source { connector_id = "web-search" }
        configuration {
          name        = "WebSearch"
          description = "Search the public web (served inside AWS by AgentCore Web Search). Use for current facts, docs and news."
          # The API rejects a configuration without parameterValues ("Connector configurations must not be empty").
          parameter_values = jsonencode({})
        }
      }
    }
  }
  credential_provider_configuration {
    gateway_iam_role {}
  }
  depends_on = [terraform_data.websearch_role_propagation]
}

output "websearch_mcp_url" { value = aws_bedrockagentcore_gateway.websearch.gateway_url }
