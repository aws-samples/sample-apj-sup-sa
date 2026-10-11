#!/usr/bin/env bash
# Remove the data-layer resources created during the first (us-east-1) build,
# which was superseded by the ap-south-1 rebuild for DPDP data localisation.
#
# DESTRUCTIVE. Deletes:
#   - Neptune instance + cluster  hvi-health-kg / hvi-health-kg-1   (~$49/mo)
#   - OpenSearch domain           hvi-logs                          (~$26/mo)
#   - DynamoDB tables             hvi-*                             (negligible, but tidy)
#   - Security groups             hvi-lambda-sg, hvi-neptune-sg
#   - Neptune subnet group        hvi-neptune-subnets
#
# It does NOT touch:
#   - the shared default VPC or its route tables (we only ever added to it)
#   - the Chime SDK Voice Connector, SIP media application, or phone numbers,
#     which stay in us-east-1 because that is where the telephony edge lives
#   - IAM roles, which are global and reused by the ap-south-1 build
#
# Run with CONFIRM=yes to proceed:
#   CONFIRM=yes bash scripts/98-teardown-us-east-1.sh

set -uo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

OLD_REGION="us-east-1"

if [ "${CONFIRM:-no}" != "yes" ]; then
  cat <<EOF

This will DELETE the following in ${OLD_REGION}:

  Neptune instance   hvi-health-kg-1
  Neptune cluster    hvi-health-kg
  OpenSearch domain  hvi-logs
  DynamoDB tables    hvi-call-state, hvi-call-session, hvi-crm-customers,
                     hvi-form-schema, hvi-intake-responses
  Security groups    hvi-lambda-sg, hvi-neptune-sg
  Subnet group       hvi-neptune-subnets

It will NOT touch the Chime SDK Voice Connector, the SIP media application,
the phone numbers, the default VPC, or any IAM role.

Re-run with:  CONFIRM=yes bash scripts/98-teardown-us-east-1.sh

EOF
  exit 1
fi

hvi_warn "tearing down ${OLD_REGION} data layer"

# ---- Neptune ---------------------------------------------------------------
if aws neptune describe-db-instances --db-instance-identifier hvi-health-kg-1 \
     --region "$OLD_REGION" >/dev/null 2>&1; then
  hvi_log "deleting Neptune instance hvi-health-kg-1"
  aws neptune delete-db-instance --db-instance-identifier hvi-health-kg-1 \
    --region "$OLD_REGION" --query 'DBInstance.DBInstanceStatus' --output text || true
  hvi_log "waiting for instance deletion (a few minutes)"
  aws neptune wait db-instance-deleted --db-instance-identifier hvi-health-kg-1 \
    --region "$OLD_REGION" 2>/dev/null || hvi_warn "wait ended; continuing"
fi

if aws neptune describe-db-clusters --db-cluster-identifier hvi-health-kg \
     --region "$OLD_REGION" >/dev/null 2>&1; then
  hvi_log "deleting Neptune cluster hvi-health-kg"
  aws neptune delete-db-cluster --db-cluster-identifier hvi-health-kg \
    --skip-final-snapshot --region "$OLD_REGION" \
    --query 'DBCluster.Status' --output text || true
fi

# ---- OpenSearch ------------------------------------------------------------
if aws opensearch describe-domain --domain-name hvi-logs --region "$OLD_REGION" >/dev/null 2>&1; then
  hvi_log "deleting OpenSearch domain hvi-logs"
  aws opensearch delete-domain --domain-name hvi-logs --region "$OLD_REGION" \
    --query 'DomainStatus.Deleted' --output text || true
fi

# ---- DynamoDB --------------------------------------------------------------
for t in hvi-call-state hvi-call-session hvi-crm-customers hvi-form-schema hvi-intake-responses; do
  if aws dynamodb describe-table --table-name "$t" --region "$OLD_REGION" >/dev/null 2>&1; then
    hvi_log "deleting table $t"
    aws dynamodb delete-table --table-name "$t" --region "$OLD_REGION" \
      --query 'TableDescription.TableStatus' --output text >/dev/null || true
  fi
done

# ---- Networking leftovers --------------------------------------------------
if aws neptune describe-db-subnet-groups --db-subnet-group-name hvi-neptune-subnets \
     --region "$OLD_REGION" >/dev/null 2>&1; then
  hvi_log "deleting Neptune subnet group (after cluster is gone)"
  aws neptune delete-db-subnet-group --db-subnet-group-name hvi-neptune-subnets \
    --region "$OLD_REGION" 2>/dev/null || hvi_warn "subnet group still in use; re-run later"
fi

for sg in hvi-neptune-sg hvi-lambda-sg; do
  sgid=$(aws ec2 describe-security-groups --region "$OLD_REGION" \
    --filters "Name=group-name,Values=${sg}" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
  if [ "$sgid" != "None" ] && [ -n "$sgid" ]; then
    hvi_log "deleting security group $sg ($sgid)"
    aws ec2 delete-security-group --group-id "$sgid" --region "$OLD_REGION" 2>/dev/null \
      || hvi_warn "$sg still referenced; re-run after Neptune finishes deleting"
  fi
done

hvi_log "teardown of ${OLD_REGION} data layer complete."
hvi_log "Telephony (Voice Connector, SMA, phone numbers) intentionally left in place."
