#!/usr/bin/env bash
# Cognito User Pool + Hosted UI + public SPA app client for the browser (WebRTC)
# voice surface. This is the identity plane docs/WEB_DEPLOY.md calls for:
# per-user login replacing the shared HVI_WEB_TOKEN, and — the part that actually
# matters — a VERIFIED phone_number claim the media host binds the caller to, so
# an authenticated user can only intake THEIR OWN record, not any phone they type.
#
# WHY THE POOL MUST BE IN ap-south-1
# ----------------------------------
# The pool sits BESIDE THE PHI. Every store that holds health data lives in
# ap-south-1 for DPDP localisation; the directory that names the humans those
# records belong to — their phone numbers, the very claim we bind identity from —
# is health-adjacent identity data and stays in the same region. A pool in
# us-east-1 would scatter the identity plane across a border from the data it
# authorises access to. So this script pins the pool to $DATA_REGION and refuses
# to run anywhere else.
#
# WHAT THIS CREATES (all reuse-by-name, safe to re-run)
#   1. User Pool            ${PREFIX}-web-pool   (phone_number attribute enabled)
#   2. Hosted UI domain     ${PREFIX}-web-<acct> (Cognito-hosted login page)
#   3. Public SPA client    ${PREFIX}-web-spa    (NO secret; code + PKCE)
#
# WHY A PUBLIC CLIENT WITH CODE + PKCE, NOT AN IMPLICIT/SECRET CLIENT
# ------------------------------------------------------------------
# web/app.js runs in the browser, so it cannot hold a client secret — a secret
# shipped to every visitor is not a secret. The OAuth authorization-code flow
# with PKCE is the current best practice for public SPAs: the code that comes back
# on the redirect is useless without the PKCE verifier the browser generated and
# never transmitted, which closes the code-interception hole that killed the
# implicit flow. Scopes are openid (required for an id_token) + phone (so the
# phone_number claim rides in the token we verify) + profile.
#
# WHAT THIS DELIBERATELY DOES NOT DO
#   - It does not create users. See the commented one-liner in the footer.
#   - It does not touch bot_web.py or web/app.js — it only PRINTS the exact values
#     those need. Wiring is a code change, done separately.
#   - It writes discovered IDs to config/env.local.sh so later scripts can read
#     them, but changes no running service.
#
# Reference: docs/WEB_DEPLOY.md ("Auth — Cognito, and identity binds the caller").
#
# Usage
#   HVI_WEB_CALLBACK_URL=https://your.host/callback bash scripts/34-cognito.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"

# ---- guard: this pool belongs in ap-south-1, beside the PHI -----------------
if [ "$R" != "ap-south-1" ]; then
  hvi_err "DATA_REGION is '$R', not ap-south-1. The Cognito pool must sit beside"
  hvi_err "the PHI it authorises access to. Refusing to create it elsewhere."
  exit 1
fi

POOL_NAME="${PREFIX}-web-pool"
CLIENT_NAME="${PREFIX}-web-spa"
# Hosted UI domain prefixes are GLOBALLY unique across all AWS accounts, so the
# bare "${PREFIX}-web" is likely taken. Suffixing the account id keeps it stable
# per account and collision-free without a random component that would change on
# every run and defeat idempotency.
DOMAIN_PREFIX="${HVI_COGNITO_DOMAIN_PREFIX:-${PREFIX}-web-${AWS_ACCOUNT_ID}}"

# Callback URL the Hosted UI is allowed to redirect back to after login. Must
# match web/app.js's redirectUri exactly (Cognito rejects a mismatch). Defaults
# to the local dev page so a bare run is still valid; override for a real host.
CALLBACK_URL="${HVI_WEB_CALLBACK_URL:-http://localhost:8095/}"

# The phone attribute is the whole point: caller_phone() on the media host reads
# this VERIFIED claim rather than trusting client-supplied request_data.
PHONE_CLAIM="phone_number"

# ---- 1. user pool (reuse by name) ------------------------------------------
POOL_ID=$(aws cognito-idp list-user-pools --region "$R" --max-results 60 \
  --query "UserPools[?Name=='${POOL_NAME}'].Id | [0]" --output text 2>/dev/null || echo "None")

if [ -z "$POOL_ID" ] || [ "$POOL_ID" = "None" ]; then
  hvi_log "creating user pool $POOL_NAME"
  # phone_number as a standard attribute; the pool verifies it so the claim we
  # bind identity from is trustworthy, not merely self-asserted at sign-up.
  POOL_ID=$(aws cognito-idp create-user-pool --region "$R" \
    --pool-name "$POOL_NAME" \
    --auto-verified-attributes phone_number \
    --username-attributes phone_number \
    --schema '[{"Name":"phone_number","AttributeDataType":"String","Required":true,"Mutable":true}]' \
    --user-pool-tags "Project=health-voice-intake" \
    --query 'UserPool.Id' --output text)
  hvi_log "created pool $POOL_ID"
  hvi_record "cognito.pool" "$POOL_ID"
else
  hvi_warn "user pool $POOL_NAME already exists ($POOL_ID)"
fi

# ---- 2. hosted UI domain (reuse; global-unique prefix) ---------------------
EXISTING_DOMAIN=$(aws cognito-idp describe-user-pool --region "$R" \
  --user-pool-id "$POOL_ID" \
  --query 'UserPool.Domain' --output text 2>/dev/null || echo "None")

if [ -z "$EXISTING_DOMAIN" ] || [ "$EXISTING_DOMAIN" = "None" ]; then
  hvi_log "creating hosted UI domain $DOMAIN_PREFIX"
  # A prefix clash surfaces as InvalidParameterException; report it plainly
  # rather than letting the whole script abort with a raw AWS trace.
  if ! aws cognito-idp create-user-pool-domain --region "$R" \
        --user-pool-id "$POOL_ID" --domain "$DOMAIN_PREFIX" >/dev/null 2>&1; then
    hvi_err "could not claim hosted-UI domain '$DOMAIN_PREFIX' (globally unique;"
    hvi_err "it may be taken). Set HVI_COGNITO_DOMAIN_PREFIX to something unique."
    exit 1
  fi
  hvi_log "created domain $DOMAIN_PREFIX"
  hvi_record "cognito.domain" "$DOMAIN_PREFIX"
else
  DOMAIN_PREFIX="$EXISTING_DOMAIN"
  hvi_warn "hosted UI domain already set ($DOMAIN_PREFIX)"
fi
HOSTED_UI_DOMAIN="https://${DOMAIN_PREFIX}.auth.${R}.amazoncognito.com"

# ---- 3. public SPA app client (reuse by name) ------------------------------
CLIENT_ID=$(aws cognito-idp list-user-pool-clients --region "$R" \
  --user-pool-id "$POOL_ID" --max-results 60 \
  --query "UserPoolClients[?ClientName=='${CLIENT_NAME}'].ClientId | [0]" \
  --output text 2>/dev/null || echo "None")

# No --generate-secret: a browser SPA cannot keep one. code flow + PKCE is what
# makes a public client safe. Callback + logout URLs must match app.js exactly.
CLIENT_ARGS=(
  --region "$R" --user-pool-id "$POOL_ID"
  --allowed-o-auth-flows code
  --allowed-o-auth-scopes openid phone profile
  --allowed-o-auth-flows-user-pool-client
  --supported-identity-providers COGNITO
  --callback-urls "$CALLBACK_URL"
  --logout-urls "$CALLBACK_URL"
)

if [ -z "$CLIENT_ID" ] || [ "$CLIENT_ID" = "None" ]; then
  hvi_log "creating public SPA client $CLIENT_NAME"
  CLIENT_ID=$(aws cognito-idp create-user-pool-client \
    "${CLIENT_ARGS[@]}" --client-name "$CLIENT_NAME" \
    --query 'UserPoolClient.ClientId' --output text)
  hvi_log "created client $CLIENT_ID"
  hvi_record "cognito.client" "$CLIENT_ID"
else
  hvi_warn "app client $CLIENT_NAME already exists ($CLIENT_ID); updating OAuth config"
  # Re-running keeps the OAuth settings in sync with an edited CALLBACK_URL
  # without minting a new client id (which would break already-issued config).
  aws cognito-idp update-user-pool-client \
    "${CLIENT_ARGS[@]}" --client-id "$CLIENT_ID" >/dev/null
fi

# ---- persist discovered IDs for later scripts ------------------------------
touch config/env.local.sh
for kv in \
    "HVI_COGNITO_USER_POOL_ID=$POOL_ID" \
    "HVI_COGNITO_APP_CLIENT_ID=$CLIENT_ID" \
    "HVI_COGNITO_REGION=$R" \
    "HVI_COGNITO_DOMAIN=$HOSTED_UI_DOMAIN"; do
  key="${kv%%=*}"
  if grep -q "^export ${key}=" config/env.local.sh 2>/dev/null; then
    sed -i.bak "s|^export ${key}=.*|export ${kv}|" config/env.local.sh && rm -f config/env.local.sh.bak
  else
    printf 'export %s\n' "$kv" >> config/env.local.sh
  fi
done

# ---- footer: exactly what to set / run next --------------------------------
hvi_log ""
hvi_log "Cognito ready. pool=$POOL_ID client=$CLIENT_ID region=$R"
hvi_log ""
hvi_log "SET ON THE MEDIA HOST (bot_web.py reads these via os.environ):"
hvi_log "  HVI_WEB_AUTH=cognito"
hvi_log "  HVI_COGNITO_USER_POOL_ID=$POOL_ID"
hvi_log "  HVI_COGNITO_APP_CLIENT_ID=$CLIENT_ID"
hvi_log "  HVI_COGNITO_REGION=$R"
hvi_log ""
hvi_log "SET IN web/app.js (window.HVI_AUTH = { ... }):"
hvi_log "  domain:      \"$HOSTED_UI_DOMAIN\""
hvi_log "  clientId:    \"$CLIENT_ID\""
hvi_log "  region:      \"$R\""
hvi_log "  redirectUri: \"$CALLBACK_URL\""
hvi_log ""
hvi_log "The caller's phone is taken from the verified '$PHONE_CLAIM' claim, NOT"
hvi_log "from anything the browser sends. That is the identity-binding guarantee."
hvi_log ""
hvi_log "CREATE A TEST USER (uncomment and set a real phone; +91 E.164):"
hvi_log "  # aws cognito-idp admin-create-user --region $R --user-pool-id $POOL_ID \\"
hvi_log "  #   --username +911234567890 \\"
hvi_log "  #   --user-attributes Name=phone_number,Value=+911234567890 Name=phone_number_verified,Value=true \\"
hvi_log "  #   --message-action SUPPRESS"
hvi_log "  # aws cognito-idp admin-set-user-password --region $R --user-pool-id $POOL_ID \\"
hvi_log "  #   --username +911234567890 --password 'Test-Passw0rd!' --permanent"
hvi_log ""
hvi_log "Next: web/app.js Hosted-UI redirect, then scripts/35-turn.sh for the"
hvi_log "in-region TURN relay. Full plan: docs/WEB_DEPLOY.md."
