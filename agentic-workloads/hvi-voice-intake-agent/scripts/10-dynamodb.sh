#!/usr/bin/env bash
# Deploy the DynamoDB data layer for the health voice intake stack.
# Idempotent: re-running skips tables that already exist.
#
# Design notes
#  - hvi-call-state    : one item per conversation turn -> the call-drop checkpoint.
#                        PK callId (S) + SK turnIndex (N) gives ordered replay of a call.
#  - hvi-call-session  : one item per call, holds the resume pointer. GSI phone-index
#                        lets an inbound callback find the caller's unfinished session.
#                        GSI status-index lets a sweeper find dropped calls to retry.
#  - hvi-form-schema   : PK formId + SK itemId. Adding a new health question is a single
#                        PutItem -> satisfies "new items addable without redeployment".
#  - hvi-crm-customers : CRM customer data, GSI phone-index for reverse lookup.
#  - hvi-intake-responses : finalised structured submission per customer per call.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

table_exists() {
  aws dynamodb describe-table --table-name "$1" --region "$AWS_REGION" >/dev/null 2>&1
}

create_table() {
  local name="$1"; shift
  if table_exists "$name"; then
    hvi_warn "table $name already exists - skipping"
    return 0
  fi
  hvi_log "creating table $name"
  aws dynamodb create-table \
    --table-name "$name" \
    --billing-mode PAY_PER_REQUEST \
    --region "$AWS_REGION" \
    --tags Key=Project,Value=health-voice-intake Key=ManagedBy,Value=hvi-scripts \
    "$@" >/dev/null
  hvi_record "ddb.$name" "created"
}

# 1. Per-turn checkpoint table -------------------------------------------------
create_table "$TBL_CALL_STATE" \
  --attribute-definitions \
      AttributeName=callId,AttributeType=S \
      AttributeName=turnIndex,AttributeType=N \
  --key-schema \
      AttributeName=callId,KeyType=HASH \
      AttributeName=turnIndex,KeyType=RANGE

# 2. Call session / resume pointer --------------------------------------------
create_table "$TBL_CALL_SESSION" \
  --attribute-definitions \
      AttributeName=callId,AttributeType=S \
      AttributeName=phone,AttributeType=S \
      AttributeName=status,AttributeType=S \
      AttributeName=updatedAt,AttributeType=S \
  --key-schema AttributeName=callId,KeyType=HASH \
  --global-secondary-indexes '[
      {
        "IndexName": "phone-index",
        "KeySchema": [
          {"AttributeName":"phone","KeyType":"HASH"},
          {"AttributeName":"updatedAt","KeyType":"RANGE"}
        ],
        "Projection": {"ProjectionType":"ALL"}
      },
      {
        "IndexName": "status-index",
        "KeySchema": [
          {"AttributeName":"status","KeyType":"HASH"},
          {"AttributeName":"updatedAt","KeyType":"RANGE"}
        ],
        "Projection": {"ProjectionType":"ALL"}
      }
    ]'

# 3. CRM customers ------------------------------------------------------------
create_table "$TBL_CRM" \
  --attribute-definitions \
      AttributeName=customerId,AttributeType=S \
      AttributeName=phone,AttributeType=S \
  --key-schema AttributeName=customerId,KeyType=HASH \
  --global-secondary-indexes '[
      {
        "IndexName": "phone-index",
        "KeySchema": [{"AttributeName":"phone","KeyType":"HASH"}],
        "Projection": {"ProjectionType":"ALL"}
      }
    ]'

# 4. Dynamic form schema ------------------------------------------------------
create_table "$TBL_FORM_SCHEMA" \
  --attribute-definitions \
      AttributeName=formId,AttributeType=S \
      AttributeName=itemId,AttributeType=S \
  --key-schema \
      AttributeName=formId,KeyType=HASH \
      AttributeName=itemId,KeyType=RANGE

# 5. Finalised intake responses ----------------------------------------------
create_table "$TBL_RESPONSES" \
  --attribute-definitions \
      AttributeName=customerId,AttributeType=S \
      AttributeName=callId,AttributeType=S \
  --key-schema \
      AttributeName=customerId,KeyType=HASH \
      AttributeName=callId,KeyType=RANGE

# 6. DPDP consent ledger (append-only) ---------------------------------------
# Consent must exist BEFORE any processing. The pre-call gate hard-blocks on a
# miss rather than warning, because the DPDP exposure is not worth the risk.
create_table "$TBL_CONSENT" \
  --attribute-definitions \
      AttributeName=customerId,AttributeType=S \
      AttributeName=grantedAt,AttributeType=S \
  --key-schema \
      AttributeName=customerId,KeyType=HASH \
      AttributeName=grantedAt,KeyType=RANGE

# 7. TRAI attempt counter -----------------------------------------------------
# Max 3 attempts per customer per day. Fine is Rs 1,00,000 per violation, so the
# counter is authoritative rather than best-effort.
create_table "$TBL_ATTEMPTS" \
  --attribute-definitions \
      AttributeName=customerId,AttributeType=S \
      AttributeName=attemptDate,AttributeType=S \
  --key-schema \
      AttributeName=customerId,KeyType=HASH \
      AttributeName=attemptDate,KeyType=RANGE

# 8. Compliance gate audit trail ---------------------------------------------
# Every gate decision is recorded so "did you check DND before dialling?" has a
# concrete answer. This is what replaces Step Functions execution history.
create_table "$TBL_AUDIT" \
  --attribute-definitions \
      AttributeName=callId,AttributeType=S \
      AttributeName=checkedAt,AttributeType=S \
  --key-schema \
      AttributeName=callId,KeyType=HASH \
      AttributeName=checkedAt,KeyType=RANGE

# Wait for all tables to become ACTIVE ---------------------------------------
for t in "$TBL_CALL_STATE" "$TBL_CALL_SESSION" "$TBL_CRM" "$TBL_FORM_SCHEMA" \
         "$TBL_RESPONSES" "$TBL_CONSENT" "$TBL_ATTEMPTS" "$TBL_AUDIT"; do
  hvi_log "waiting for $t to become ACTIVE"
  aws dynamodb wait table-exists --table-name "$t" --region "$AWS_REGION"
done

# Enable TTL on the per-turn table so demo data self-cleans after 30 days.
if ! aws dynamodb describe-time-to-live --table-name "$TBL_CALL_STATE" --region "$AWS_REGION" \
      --query 'TimeToLiveDescription.TimeToLiveStatus' --output text 2>/dev/null | grep -q ENABLED; then
  hvi_log "enabling TTL on $TBL_CALL_STATE (attribute: expiresAt)"
  aws dynamodb update-time-to-live \
    --table-name "$TBL_CALL_STATE" \
    --time-to-live-specification "Enabled=true,AttributeName=expiresAt" \
    --region "$AWS_REGION" >/dev/null
fi

hvi_log "DynamoDB data layer ready."
aws dynamodb list-tables --region "$AWS_REGION" \
  --query "TableNames[?starts_with(@,'${PREFIX}-')]" --output table
