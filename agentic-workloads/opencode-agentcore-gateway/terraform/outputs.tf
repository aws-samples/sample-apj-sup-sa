output "region" { value = var.region }
output "gateway_id" { value = aws_bedrockagentcore_gateway.gw.gateway_id }
output "gateway_url" { value = aws_bedrockagentcore_gateway.gw.gateway_url }
output "gateway_base_url" {
  description = "Gateway origin for ocgw (gateway_url in ~/.config/ocgw/config.json and mdm/render.py --gateway-url)"
  value       = "https://${aws_bedrockagentcore_gateway.gw.gateway_id}.gateway.bedrock-agentcore.${var.region}.amazonaws.com"
}
output "cognito_user_pool_id" { value = local.cognito ? aws_cognito_user_pool.idp[0].id : null }
output "cognito_client_id" { value = local.cognito ? aws_cognito_user_pool_client.cli[0].id : null }
output "ocgw_config" {
  description = "~/.config/ocgw/config.json for the Cognito stand-in (Okta and JumpCloud: see docs/okta.md, docs/jumpcloud.md)"
  # Cognito access tokens carry client_id and username, which the gateway authorizer and user_claim check; Cognito
  # does not accept the offline_access scope (the code flow returns a refresh token without it).
  value = local.cognito ? jsonencode({
    issuer        = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.idp[0].id}"
    client_id     = aws_cognito_user_pool_client.cli[0].id
    token_kind    = "access_token"
    scope         = "openid email profile"
    gateway_url   = "https://${aws_bedrockagentcore_gateway.gw.gateway_id}.gateway.bedrock-agentcore.${var.region}.amazonaws.com"
    websearch_url = aws_bedrockagentcore_gateway.websearch.gateway_url
  }) : null
}
output "users" { value = var.users }
output "passwords" {
  value     = { for u, p in random_password.u : u => p.result }
  sensitive = true
}
output "usage_table" { value = aws_dynamodb_table.usage.name }
output "openweight_models" { value = var.openweight_models }
