#!/usr/bin/env bash
# Provision the Neptune cluster that holds the health-condition knowledge graph.
#
# Cost note: db.t4g.medium is the smallest class Neptune supports and runs at
# roughly $0.067/hr on-demand (~$49/mo) plus storage and I/O. Delete the cluster
# with scripts/99-teardown.sh when the demo is finished.
#
# IAM database authentication is enabled, which is why hvi-lambda-role and
# hvi-agentcore-role were granted neptune-db:* rather than a password.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

CLUSTER_ID="${PREFIX}-health-kg"
INSTANCE_ID="${CLUSTER_ID}-1"
INSTANCE_CLASS="${NEPTUNE_INSTANCE_CLASS:-db.t4g.medium}"

: "${NEPTUNE_SUBNET_GROUP:?run scripts/20-vpc.sh first}"
: "${SG_NEPTUNE:?run scripts/20-vpc.sh first}"

# ---- Cluster ----------------------------------------------------------------
if aws neptune describe-db-clusters --db-cluster-identifier "$CLUSTER_ID" \
     --region "$AWS_REGION" >/dev/null 2>&1; then
  hvi_warn "Neptune cluster $CLUSTER_ID already exists"
else
  hvi_log "creating Neptune cluster $CLUSTER_ID"
  aws neptune create-db-cluster \
    --db-cluster-identifier "$CLUSTER_ID" \
    --engine neptune \
    --db-subnet-group-name "$NEPTUNE_SUBNET_GROUP" \
    --vpc-security-group-ids "$SG_NEPTUNE" \
    --enable-iam-database-authentication \
    --storage-encrypted \
    --backup-retention-period 1 \
    --port 8182 \
    --region "$AWS_REGION" \
    --tags Key=Project,Value=health-voice-intake \
    --query 'DBCluster.DBClusterIdentifier' --output text
  hvi_record "neptune.cluster" "$CLUSTER_ID"
fi

# ---- Writer instance --------------------------------------------------------
if aws neptune describe-db-instances --db-instance-identifier "$INSTANCE_ID" \
     --region "$AWS_REGION" >/dev/null 2>&1; then
  hvi_warn "Neptune instance $INSTANCE_ID already exists"
else
  hvi_log "creating Neptune instance $INSTANCE_ID ($INSTANCE_CLASS)"
  aws neptune create-db-instance \
    --db-instance-identifier "$INSTANCE_ID" \
    --db-instance-class "$INSTANCE_CLASS" \
    --engine neptune \
    --db-cluster-identifier "$CLUSTER_ID" \
    --region "$AWS_REGION" \
    --tags Key=Project,Value=health-voice-intake \
    --query 'DBInstance.DBInstanceIdentifier' --output text
  hvi_record "neptune.instance" "$INSTANCE_ID"
fi

hvi_log "Neptune is provisioning in the background (typically 8-15 minutes)."
hvi_log "Check status with: bash scripts/status.sh"
