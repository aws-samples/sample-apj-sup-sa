# --- Outputs ---
output "api_url" {
  value = aws_apigatewayv2_api.api.api_endpoint
}

output "get_users_log_group" {
  value = aws_cloudwatch_log_group.get_users.name
}
