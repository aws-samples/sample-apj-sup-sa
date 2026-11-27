variable "aws_profile" {
  description = "AWS CLI profile. null = the default credential chain (env keys, SSO, instance role)"
  type        = string
  default     = null
}

variable "region" {
  description = "AWS Region. Must offer Amazon Bedrock Mantle and AgentCore Gateway inference targets"
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Prefix for every resource (lowercase letters, digits and hyphens, at most 20 characters)"
  type        = string
  default     = "ocgw"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.name))
    error_message = "name must be 2-20 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "python" {
  description = "Python with boto3 >= 1.43.118, used by the helpers. Empty = chosen when each command runs by helpers/python.sh: ../.venv/bin/python, else python3 (each only with boto3 >= 1.43.118 and requests), else uv with them"
  type        = string
  default     = ""
}

# ---- Identity provider ----
# okta and jumpcloud read the issuer from the OIDC discovery URL you set. cognito creates a stand-in user pool with
# the test users below, so the stack can be tried without an external IdP.
variable "idp" {
  description = "Identity provider: okta, jumpcloud or cognito"
  type        = string
  default     = "cognito"
  validation {
    condition     = contains(["okta", "jumpcloud", "cognito"], var.idp)
    error_message = "idp must be okta, jumpcloud or cognito."
  }
}

variable "oidc_discovery_url" {
  description = "OIDC discovery URL (required for okta and jumpcloud). Okta: https://<org>.okta.com/oauth2/<server>/.well-known/openid-configuration. JumpCloud: https://oauth.id.jumpcloud.com/.well-known/openid-configuration"
  type        = string
  default     = ""
  validation {
    condition     = var.oidc_discovery_url == "" || can(regex("^https://[^/]+/.*\\.well-known/openid-configuration$", var.oidc_discovery_url))
    error_message = "oidc_discovery_url must be an https URL ending in .well-known/openid-configuration."
  }
}

variable "oidc_client_id" {
  description = "OIDC client ID of the native (public, PKCE) app the ocgw sign-in helper uses"
  type        = string
  default     = ""
}

variable "oidc_allowed_clients" {
  description = "Accepted client_id claim values. Empty = oidc_client_id (or the Cognito client). Do not also set audiences: the gateway ANDs them."
  type        = list(string)
  default     = []
}

variable "oidc_allowed_audience" {
  description = "Accepted aud values (Okta custom authorization servers and JumpCloud put the audience in aud)"
  type        = list(string)
  default     = []
}

variable "user_claim" {
  description = "JWT claim that names the user. Cognito: username. Okta and JumpCloud: sub, mapped to ledger users through identity_map. Never use a claim the user can edit, such as email"
  type        = string
  default     = "username"
}

variable "group_claim" {
  description = "JWT claim that carries the user's groups. Cognito: cognito:groups. Okta: groups (add a groups claim). JumpCloud: the group attribute name set on the app (docs/jumpcloud.md uses groups)"
  type        = string
  default     = "cognito:groups"
}

# ---- Users and groups ----
variable "cognito_extra_users" {
  description = "With idp = cognito: extra test users created with no group and no ledger entry (refused by the gateway; the test matrix uses nogroup)"
  type        = list(string)
  default     = ["nogroup"]
  validation {
    condition     = alltrue([for u in var.cognito_extra_users : can(regex("^[A-Za-z0-9._@+-]{1,128}$", u)) && !contains(keys(var.users), u)])
    error_message = "cognito_extra_users names must be valid user names that are not in users."
  }
}

variable "users" {
  description = "Ledger users (budget, Mantle project, rate limits) and their group. With idp = cognito these are also created as test users"
  type        = map(string)
  default = {
    alice = "ocgw-team-a"
    bob   = "ocgw-team-b"
    carol = "ocgw-team-b"
  }
  validation {
    # The dashboard opens and edits users by these names (dashboard_api NAME), so a name it would refuse is refused here.
    # Groups follow the dashboard's GROUP_NAME rule (no control characters, / \\ or ,), so every seeded group can be opened.
    condition     = alltrue([for u, g in var.users : can(regex("^[A-Za-z0-9._@+-]{1,128}$", u)) && can(regex("^[^\\x00-\\x1f\\x7f/\\\\,]{1,256}$", g)) && g == trimspace(g)])
    error_message = "users keys must be 1-128 characters of letters, digits and . _ @ + -, and each group 1-256 characters without control characters, /, \\ or a comma, and no leading or trailing spaces."
  }
  validation {
    # Cognito's own group-name rule, stricter than the dashboard's: checked here so plan explains it in one place.
    condition     = var.idp != "cognito" || alltrue([for g in values(var.users) : can(regex("^[^\\s]{1,128}$", g))])
    error_message = "With idp = cognito, each group in var.users must be 1-128 characters with no spaces (Cognito's rule)."
  }
}

variable "group_models" {
  description = "Model allow-list per group. Values are the model IDs opencode sends (ow/<alias>). A user in several groups gets the union"
  type        = map(list(string))
  default = {
    "ocgw-team-a" = ["ow/kimi-k3", "ow/glm-5.3"]
    "ocgw-team-b" = ["ow/kimi-k3", "ow/glm-5.3", "ow/deepseek-v3.2", "ow/glm-5"]
  }
  validation {
    condition     = alltrue(flatten([for g, ms in var.group_models : [for m in ms : startswith(m, "ow/")]]))
    error_message = "group_models values must be ow/<alias> model IDs."
  }
}

variable "identity_map" {
  description = "IdP user id (the user_claim value, use the immutable sub) -> ledger user name in var.users. Required for okta and jumpcloud: ids not in the map are refused. Leave empty for cognito"
  type        = map(string)
  default     = {}
}

# ---- Budgets (USD) ----
variable "default_budget_usd" {
  description = "Monthly budget per user in USD"
  type        = number
  default     = 50
  validation {
    condition     = var.default_budget_usd >= 0
    error_message = "default_budget_usd must be 0 or more (0 blocks every call)."
  }
}

variable "default_daily_usd" {
  description = "Daily budget per user in USD for everyone without a group, org or user limit (null = no daily cap)"
  type        = number
  default     = null
  validation {
    condition     = var.default_daily_usd == null || try(var.default_daily_usd >= 0, false)
    error_message = "default_daily_usd must be null or 0 or more."
  }
}

variable "default_hourly_usd" {
  description = "Hourly budget per user in USD for everyone without a group, org or user limit (null = no hourly cap)"
  type        = number
  default     = null
  validation {
    condition     = var.default_hourly_usd == null || try(var.default_hourly_usd >= 0, false)
    error_message = "default_hourly_usd must be null or 0 or more."
  }
}

variable "default_rpm" {
  description = "Requests per minute per user in the interceptor for everyone without a group, org or user limit (null = none; the gateway's rpm_default still applies)"
  type        = number
  default     = null
  validation {
    condition     = var.default_rpm == null || try(var.default_rpm >= 1 && floor(var.default_rpm) == var.default_rpm, false)
    error_message = "default_rpm must be null or a whole number of 1 or more."
  }
}

locals {
  # The deployment defaults the interceptor and the dashboard read the same way (policy.env_limits); "" = not set.
  default_limits_env = {
    DEFAULT_BUDGET_USD = tostring(var.default_budget_usd)
    DEFAULT_DAILY_USD  = var.default_daily_usd == null ? "" : tostring(var.default_daily_usd)
    DEFAULT_HOURLY_USD = var.default_hourly_usd == null ? "" : tostring(var.default_hourly_usd)
    DEFAULT_RPM        = var.default_rpm == null ? "" : tostring(var.default_rpm)
  }
}

variable "budget_overrides_usd" {
  description = "Per-user monthly budget in USD, written when the user's BUDGET item is first created; after that the dashboard owns it (Terraform ignores later changes here, see ledger.tf). carol gets a small one so the test suite can show a refusal"
  type        = map(number)
  default     = { carol = 0.02 }
  validation {
    # The dashboard's rule: 0 blocks the user, otherwise a cent or more (a cap between would block like 0, unnoticed).
    condition     = alltrue([for u, v in var.budget_overrides_usd : contains(keys(var.users), u) && (v == 0 || (v >= 0.01 && v <= 100000))])
    error_message = "budget_overrides_usd values must be 0 or from 0.01 to 100000, and each key must be a user in var.users."
  }
}

# ---- Gateway rate limits, keyed by the user claim ----
# A change to these can take minutes to be enforced, even once the gateway reports the limit ACTIVE (it lets requests
# through meanwhile; see docs/dashboard.md). For a cap that must hold the moment it is set, use the interceptor's rpm.
variable "rpm_default" {
  description = "Requests per minute per user at the gateway (a change can take minutes to be enforced)"
  type        = number
  default     = 60
  validation {
    condition     = var.rpm_default >= 1 && floor(var.rpm_default) == var.rpm_default
    error_message = "rpm_default must be a whole number of at least 1."
  }
}

variable "tpm_default" {
  description = "Tokens per minute per user at the gateway (a change can take minutes to be enforced)"
  type        = number
  default     = 2000000
  validation {
    condition     = var.tpm_default >= 1 && floor(var.tpm_default) == var.tpm_default
    error_message = "tpm_default must be a whole number of at least 1."
  }
}

variable "rpm_overrides" {
  description = "Per-user requests per minute at the gateway (a change can take minutes to be enforced). carol gets 2 so the test suite can show a 429"
  type        = map(number)
  default     = { carol = 2 }
  validation {
    # As rpm_default: a whole number of requests per minute; and only for users this gateway knows.
    condition     = alltrue([for u, v in var.rpm_overrides : contains(keys(var.users), u) && v >= 1 && v <= 100000 && floor(v) == v])
    error_message = "rpm_overrides keys must be users in var.users, and each value a whole number from 1 to 100000."
  }
  validation {
    # The gateway matches its entries on the token's claim: with an identity_map that is an IdP id.
    condition     = length(var.identity_map) == 0 || alltrue([for u in keys(var.rpm_overrides) : contains(values(var.identity_map), u)])
    error_message = "With identity_map set, every rpm_overrides user needs an IdP id in identity_map (the gateway limit matches on it)."
  }
}

# ---- Models ----
# Key = alias opencode sends (as "ow/<alias>"); id = Bedrock Mantle model ID.
# Prices: USD per 1M tokens from the AWS Price List API (pricing:GetProducts, AmazonBedrock, us-east-1) unless src says
# BILLED. Mantle reports no cache split, so all input is priced at the input rate, or at cache_write (optional, USD per
# 1M cache-write tokens) when that is higher: set it for a model whose cache writes cost more than its input, or
# reported spend can be below the bill. The cache-write rates of kimi-k3 and glm-5.3 (named in src) are not checked.
# api = API from the model card: "chat" (Chat Completions) or "responses". opencode uses Chat Completions through its
# OpenAI-compatible provider, so every model here must support chat.
variable "openweight_models" {
  type = map(object({ id = string, in = number, out = number, cache_write = optional(number), maxOut = number, src = string, api = optional(string, "chat") }))
  default = {
    "kimi-k3"       = { id = "moonshotai.kimi-k3", in = 3.30, out = 16.50, maxOut = 32768, src = "USE1-moonshotai.kimi-k3-mantle-{input,output}-tokens-standard, -cache-read-tokens-standard, -cache-write-tokens-30m-standard" }
    "glm-5.3"       = { id = "zai.glm-5.3", in = 1.848, out = 5.808, maxOut = 32768, src = "BILLED rate: Cost Explorer USE1-zai.glm-5.3-mantle-{input,output,cache-read,cache-write-30m}-tokens-standard, Oct 2026" }
    "deepseek-v3.2" = { id = "deepseek.v3.2", in = 0.62, out = 1.85, maxOut = 16384, src = "USE1-deepseek.v3.2-{input,output}-tokens" }
    "glm-5"         = { id = "zai.glm-5", in = 1.00, out = 3.20, maxOut = 32768, src = "USE1-zai.glm-5-mantle-{input,output}-tokens" }
  }
  validation {
    condition     = alltrue([for k, m in var.openweight_models : can(regex("^[a-z0-9][a-z0-9.-]*$", k)) && m.in >= 0 && m.out >= 0 && (m.cache_write == null ? true : m.cache_write >= 0) && contains(["chat", "responses"], m.api)])
    error_message = "openweight_models keys must be lowercase aliases; prices (cache_write too) must be >= 0; api must be chat or responses."
  }
  validation {
    # The interceptor rewrites ow/<alias> to the Mantle id, and fallback rules, prices and usage are keyed by that id:
    # two aliases for one id would share them, so a group allowed only one alias could be served the other's fallback.
    condition     = length(distinct([for m in values(var.openweight_models) : m.id])) == length(var.openweight_models)
    error_message = "openweight_models ids must be distinct: give each Mantle model one alias."
  }
}

variable "tag_dimensions" {
  description = "Tag dimensions that usage_sync rolls up per month as TAG#<dim>=<value>, from each user's tags in user_tags"
  type        = list(string)
  default     = ["team", "project", "environment"]
}

variable "user_tags" {
  description = "Tags per ledger user for cost attribution, for example { alice = { team = \"payments\", project = \"checkout\" } }. A dimension a user has no tag for rolls up as \"unassigned\""
  type        = map(map(string))
  default     = {}
  validation {
    condition     = alltrue([for u, t in var.user_tags : contains(keys(var.users), u) && alltrue([for k, v in t : can(regex("^[A-Za-z0-9._-]{1,64}$", k)) && length(v) > 0 && length(v) <= 128])])
    error_message = "user_tags keys must be users in var.users; tag names [A-Za-z0-9._-] up to 64 characters; values 1-128 characters."
  }
}

variable "gateway_debug_errors" {
  description = "Return detailed gateway exception text to callers (exception_level DEBUG). Off by default; turn on only while debugging"
  type        = bool
  default     = false
}
