#!/usr/bin/env bash
# Provision the durable per-call archive bucket, and let the runtime write to it.
#
# WHY THIS BUCKET EXISTS
# The turn-by-turn transcript was only ever written to hvi-call-state, which
# carries a 30-day TTL, and Salesforce's Transcript_URI__c / Recording_URI__c were
# written blank on every call because nothing produced an artefact to point at.
# So the evidence behind an underwriting decision deleted itself a month after the
# call while the decision itself lived on. This is the durable copy: one
# transcript per call, in-region, linked from the CRM record.
#
# DELIBERATELY NO OBJECT LOCK
# Object Lock is the reflex for an audit trail and it is the wrong tool here. It
# enforces retention by making deletion impossible - including a deletion this
# project may be legally required to perform, since DPDP gives the customer
# erasure rights and a compliance-mode lock leaves no lawful way to honour one.
# It also cannot be added to an existing bucket, so choosing it is irreversible.
#
# What is used instead, and what each part actually buys:
#   versioning            an overwrite or delete is recoverable, not silent
#   SSE (AES256)          encrypted at rest
#   TLS-only policy       encrypted in transit, enforced rather than assumed
#   public-access block   no path to anonymous read
#   ap-south-1            health data stays in India (DPDP)
# Recoverability without immutability, which is the right trade for a system that
# may be required to delete.
#
# Usage
#   bash scripts/55-call-archive.sh

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"
ACC="$AWS_ACCOUNT_ID"
BUCKET="${CALL_ARCHIVE_BUCKET:-${PREFIX}-archive-${ACC}-${R}}"

hvi_log "archive bucket: $BUCKET  (region $R)"

if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  hvi_log "bucket exists"
else
  hvi_log "creating bucket"
  # NOTE: no --object-lock-enabled-for-bucket. See the header.
  aws s3api create-bucket --bucket "$BUCKET" --region "$R" \
    --create-bucket-configuration "LocationConstraint=$R" >/dev/null
fi

# Applied on EVERY run rather than only at creation, so a bucket that predates a
# setting still ends up with it. Each of these is idempotent.
hvi_log "blocking public access"
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

hvi_log "enabling default encryption (AES256)"
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'

hvi_log "enabling versioning (this is the Object Lock substitute)"
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration "Status=Enabled"

# A Deny-only statement is not a grant, so it does not trip BlockPublicPolicy.
hvi_log "denying non-TLS access"
aws s3api put-bucket-policy --bucket "$BUCKET" \
  --policy "$(jq -nc --arg b "$BUCKET" \
    '{Version:"2012-10-17",
      Statement:[{Sid:"DenyInsecureTransport",Effect:"Deny",Principal:"*",
                  Action:"s3:*",
                  Resource:["arn:aws:s3:::\($b)","arn:aws:s3:::\($b)/*"],
                  Condition:{Bool:{"aws:SecureTransport":"false"}}}]}')"

# NO storage-class transitions, on purpose. A transcript is a few kilobytes and
# STANDARD_IA bills a 128KB minimum per object, so tiering these would cost more
# than it saves while adding a retrieval story for no benefit. What IS worth
# doing is bounding the debris: abandoned multipart uploads, and the old versions
# that versioning necessarily accumulates when a call is re-archived.
hvi_log "setting lifecycle (debris only, no transitions)"
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" \
  --lifecycle-configuration '{
    "Rules": [
      {"ID": "abort-incomplete-uploads", "Status": "Enabled",
       "Filter": {"Prefix": ""},
       "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}},
      {"ID": "expire-superseded-versions", "Status": "Enabled",
       "Filter": {"Prefix": "calls/"},
       "NoncurrentVersionExpiration": {"NoncurrentDays": 365}}
    ]}'

# ---------------------------------------------------------------------------
# Let the runtime write to it
# ---------------------------------------------------------------------------
# The Lambda role is already covered: scripts/15-iam.sh grants it s3 on
# ${PREFIX}-archive-${ACC}-*, which is why this bucket is named -archive-. The
# AgentCore role has no S3 statement at all, and the archive is written from
# agent/tools.py submit_to_crm, so it needs one.
AC_ROLE="${PREFIX}-agentcore-role"
if aws iam get-role --role-name "$AC_ROLE" >/dev/null 2>&1; then
  hvi_log "granting $AC_ROLE write access to the archive"
  aws iam put-role-policy --role-name "$AC_ROLE" \
    --policy-name "${PREFIX}-call-archive-write" \
    --policy-document "$(jq -nc --arg b "$BUCKET" \
      '{Version:"2012-10-17",
        Statement:[{Sid:"WriteCallArchive",Effect:"Allow",
                    Action:["s3:PutObject","s3:GetObject"],
                    Resource:["arn:aws:s3:::\($b)/*"]},
                   {Sid:"ListCallArchive",Effect:"Allow",
                    Action:["s3:ListBucket"],
                    Resource:["arn:aws:s3:::\($b)"]}]}')"
else
  hvi_warn "role $AC_ROLE not found - run scripts/15-iam.sh first"
fi

# ---------------------------------------------------------------------------
# Prove what was actually applied, rather than trusting the calls above
# ---------------------------------------------------------------------------
echo
hvi_log "verifying"
LOCK=$(aws s3api get-object-lock-configuration --bucket "$BUCKET" \
  --query 'ObjectLockConfiguration.ObjectLockEnabled' --output text 2>/dev/null \
  || echo "NOT-CONFIGURED")
VERS=$(aws s3api get-bucket-versioning --bucket "$BUCKET" \
  --query 'Status' --output text 2>/dev/null || echo NONE)
ENC=$(aws s3api get-bucket-encryption --bucket "$BUCKET" \
  --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' \
  --output text 2>/dev/null || echo NONE)
hvi_log "  object lock : $LOCK   (NOT-CONFIGURED is the intent)"
hvi_log "  versioning  : $VERS"
hvi_log "  encryption  : $ENC"

touch config/env.local.sh
if grep -q '^export CALL_ARCHIVE_BUCKET=' config/env.local.sh 2>/dev/null; then
  hvi_warn "CALL_ARCHIVE_BUCKET already in config/env.local.sh"
else
  printf 'export CALL_ARCHIVE_BUCKET="%s"\n' "$BUCKET" >> config/env.local.sh
  hvi_log "appended CALL_ARCHIVE_BUCKET to config/env.local.sh"
fi

hvi_log ""
hvi_log "next: redeploy the agent so submit_to_crm can write the archive"
hvi_log "  bash scripts/40-agentcore.sh"
