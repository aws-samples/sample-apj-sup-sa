# Identity. With idp = okta or jumpcloud the gateway trusts that IdP's issuer (var.oidc_discovery_url) and nothing here
# is created. With idp = cognito (the default, for a first try without an external IdP) a Cognito user pool stands in,
# with the test users and groups in var.users.

locals {
  cognito = var.idp == "cognito"
}

resource "aws_cognito_user_pool" "idp" {
  count = local.cognito ? 1 : 0
  # Stand-in IdP: accounts are created by Terraform/admins only, no self sign-up.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }
  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 1
  }
  deletion_protection = "INACTIVE" # stand-in pool; destroyed with the stack
  name                = "${var.name}-idp"
  tags                = { Component = "identity" }
}

# Hosted sign-in for `ocgw login` (authorization code + PKCE). The prefix must be unique in the Region.
resource "random_id" "cognito_domain" {
  count       = local.cognito ? 1 : 0
  byte_length = 4
}

resource "aws_cognito_user_pool_domain" "idp" {
  count        = local.cognito ? 1 : 0
  domain       = "${var.name}-${random_id.cognito_domain[0].hex}"
  user_pool_id = aws_cognito_user_pool.idp[0].id
}

resource "aws_cognito_user_pool_client" "cli" {
  count        = local.cognito ? 1 : 0
  name         = "${var.name}-opencode"
  user_pool_id = aws_cognito_user_pool.idp[0].id
  # A public client (no secret): `ocgw login` uses the code flow with PKCE and the loopback redirect; the password
  # flow stays for the scripted end-to-end tests.
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  callback_urls                        = ["http://127.0.0.1:8765/callback"]
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  access_token_validity                = 60
  id_token_validity                    = 60
  token_validity_units {
    access_token = "minutes"
    id_token     = "minutes"
  }
  prevent_user_existence_errors = "ENABLED"
}

resource "aws_cognito_user_group" "g" {
  for_each     = local.cognito ? toset(distinct(values(var.users))) : toset([])
  name         = each.key
  user_pool_id = aws_cognito_user_pool.idp[0].id # names checked against Cognito's rule by var.users' validation
}

# Cognito test users: every ledger user, plus cognito_extra_users (no group, no ledger entry), which the test matrix
# signs in as to check that a user without a group is refused.
locals {
  cognito_users = local.cognito ? merge(var.users, { for u in var.cognito_extra_users : u => "" }) : {}
}

resource "random_password" "u" {
  for_each         = local.cognito_users
  length           = 20
  min_upper        = 1
  min_lower        = 1
  min_numeric      = 1
  min_special      = 1
  override_special = "!#%*-_"
}

resource "aws_cognito_user" "u" {
  for_each       = local.cognito_users
  user_pool_id   = aws_cognito_user_pool.idp[0].id
  username       = each.key
  password       = random_password.u[each.key].result
  message_action = "SUPPRESS"
}

resource "aws_cognito_user_in_group" "u" {
  for_each     = local.cognito ? var.users : {}
  user_pool_id = aws_cognito_user_pool.idp[0].id
  group_name   = aws_cognito_user_group.g[each.value].name
  username     = aws_cognito_user.u[each.key].username
}

locals {
  discovery_url = local.cognito ? "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.idp[0].id}/.well-known/openid-configuration" : var.oidc_discovery_url
  # Okta and JumpCloud: client_id (Okta access tokens carry cid, so prefer audience there) or audience, never both.
  allowed_clients = length(var.oidc_allowed_clients) > 0 ? var.oidc_allowed_clients : (
    length(var.oidc_allowed_audience) > 0 ? [] : (local.cognito ? [aws_cognito_user_pool_client.cli[0].id] : compact([var.oidc_client_id]))
  )
}

# Identity settings that must hold before anything is deployed. Preconditions stop the plan (a `check` block only
# warns), so a gateway keyed on a claim the user can edit, or with no identity_map, is never applied.
resource "terraform_data" "idp_settings" {
  lifecycle {
    precondition {
      condition     = local.cognito || (var.oidc_discovery_url != "" && (length(var.oidc_allowed_audience) > 0 || length(local.allowed_clients) > 0))
      error_message = "idp = okta or jumpcloud needs oidc_discovery_url and an audience (oidc_allowed_audience) or client (oidc_client_id / oidc_allowed_clients)."
    }
    precondition {
      condition     = local.cognito || (length(var.identity_map) > 0 && alltrue([for u in values(var.identity_map) : contains(keys(var.users), u)]))
      error_message = "idp = okta or jumpcloud needs identity_map (IdP sub -> ledger user), and every ledger user it names must be a key of var.users."
    }
    precondition {
      condition     = local.cognito ? (var.user_claim == "username" || (var.user_claim == "sub" && length(var.identity_map) > 0)) : var.user_claim == "sub"
      error_message = "Set user_claim = \"sub\" (okta, jumpcloud) or \"username\" (cognito; \"sub\" only with identity_map, since ledger users are keyed by username). A claim the user can edit (such as email) would let one user be counted and authorized as another."
    }
  }
}
