# --- API Gateway ---
resource "aws_apigatewayv2_api" "api" {
  name          = "${var.service}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      method         = "$context.httpMethod"
      path           = "$context.path"
      status         = "$context.status"
      error          = "$context.error.message"
      integrationErr = "$context.integrationErrorMessage"
      latency        = "$context.responseLatency"
    })
  }
}

resource "aws_cloudwatch_log_group" "api_gw" {
  name              = "/aws/apigateway/${var.service}-api"
  retention_in_days = 7
}


# GET /users -> get_users
resource "aws_apigatewayv2_integration" "get_users" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.get_users.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_users" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /users"
  target    = "integrations/${aws_apigatewayv2_integration.get_users.id}"
}

resource "aws_lambda_permission" "get_users" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_users.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}


# POST /users -> create_user
resource "aws_apigatewayv2_integration" "create_user" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.create_user.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "create_user" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "POST /users"
  target    = "integrations/${aws_apigatewayv2_integration.create_user.id}"
}

resource "aws_lambda_permission" "create_user" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.create_user.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

# GET /users/{id} -> get_user
resource "aws_apigatewayv2_integration" "get_user" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.get_user.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_user" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /users/{id}"
  target    = "integrations/${aws_apigatewayv2_integration.get_user.id}"
}

resource "aws_lambda_permission" "get_user" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_user.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}


# DELETE /users/{id} -> delete_user
resource "aws_apigatewayv2_integration" "delete_user" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.delete_user.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "delete_user" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "DELETE /users/{id}"
  target    = "integrations/${aws_apigatewayv2_integration.delete_user.id}"
}

resource "aws_lambda_permission" "delete_user" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.delete_user.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}


# GET /stats -> get_stats
resource "aws_apigatewayv2_integration" "get_stats" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.get_stats.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_stats" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /stats"
  target    = "integrations/${aws_apigatewayv2_integration.get_stats.id}"
}

resource "aws_lambda_permission" "get_stats" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_stats.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
