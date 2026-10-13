#!/usr/bin/env bash
# Phase C3 — build, push, and deploy the InterviewCoach G1 hosted demo.
#
# Orchestrates the two-pass deploy that infra/g1/deploy.yaml requires:
#   1. Create the ECR repos (deploy the stack once; services stay PENDING with no image — fine).
#   2. Build + push the worker and backend images (linux/amd64) to those repos.
#   3. Populate the (empty) Deepgram secret from voice-worker/.env — NEVER committed.
#   4. Re-deploy so the services pull the images and stabilize.
#   5. Build the SPA, write the real Cognito ids into config.js, sync to S3, invalidate CloudFront.
#
# Idempotent: safe to re-run. Reads non-secret values from the gate-enablement stack outputs.
#
# Usage:   infra/g1/deploy.sh [IMAGE_TAG]
#   IMAGE_TAG defaults to "c1". Pass an explicit tag (e.g. a git short-sha) to push and deploy a
#   new image version; re-pass the SAME tag to redeploy the existing images unchanged.
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
GATE_STACK="interviewcoach-g1"
DEMO_STACK="interviewcoach-g1-demo"
IMAGE_TAG="${1:-c1}"

# DEPLOY_PHASE controls which legs run, so the CodeBuild image path (build-images.sh) slots in
# cleanly without a Ctrl-C dance:
#   all    (default) — pass-1 + local docker build/push of all three images + pass-2 + SPA
#   pass1            — pass-1 only (create stack + ECR repos at desired=0) + populate Deepgram
#                      secret, then stop. Run build-images.sh next, then DEPLOY_PHASE=finish.
#   finish           — assume images already pushed: pass-2 (scale to 1) + force-new-deploy + SPA
DEPLOY_PHASE="${DEPLOY_PHASE:-all}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\n=== %s ===\n' "$1"; }

acct() { aws sts get-caller-identity --query Account --output text --region "$REGION"; }
ACCOUNT_ID="$(acct)"
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# deploy.yaml is > 51,200 bytes, so `cloudformation deploy` must stage it through S3. Use a
# small, reusable per-account/region artifacts bucket (created once; idempotent).
CFN_ARTIFACTS_BUCKET="interviewcoach-g1-cfn-artifacts-${ACCOUNT_ID}-${REGION}"
ensure_artifacts_bucket() {
  if ! aws s3api head-bucket --bucket "$CFN_ARTIFACTS_BUCKET" --region "$REGION" >/dev/null 2>&1; then
    log "Creating CFN template-staging bucket $CFN_ARTIFACTS_BUCKET"
    if [[ "$REGION" == "us-east-1" ]]; then
      aws s3api create-bucket --bucket "$CFN_ARTIFACTS_BUCKET" --region "$REGION" >/dev/null
    else
      aws s3api create-bucket --bucket "$CFN_ARTIFACTS_BUCKET" --region "$REGION" \
        --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
    fi
    aws s3api put-public-access-block --bucket "$CFN_ARTIFACTS_BUCKET" --region "$REGION" \
      --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true >/dev/null
  fi
}

gate_out() {
  aws cloudformation describe-stacks --stack-name "$GATE_STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
demo_out() {
  aws cloudformation describe-stacks --stack-name "$DEMO_STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true
}

# ---- gather reused inputs from the gate-enablement stack -------------------------------
log "Reading gate-enablement outputs"
DB_ENDPOINT="$(gate_out DBEndpoint)"
DB_PORT="$(gate_out DBPort)"
DB_SECRET_ARN="$(gate_out DBMasterSecretArn)"
DEEPGRAM_SECRET_ARN="$(gate_out DeepgramSecretArn)"
echo "DBEndpoint=$DB_ENDPOINT  DeepgramSecretArn=$DEEPGRAM_SECRET_ARN"

# Default VPC + its public subnets + the RDS security group.
VPC_ID="$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)"
SUBNET_IDS="$(aws ec2 describe-subnets --region "$REGION" \
  --filters Name=vpc-id,Values="$VPC_ID" Name=map-public-ip-on-launch,Values=true \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')"
RDS_SG_ID="$(aws rds describe-db-instances --region "$REGION" \
  --db-instance-identifier interviewcoach-g1-latency \
  --query 'DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId' --output text)"
echo "VPC=$VPC_ID  Subnets=$SUBNET_IDS  RdsSG=$RDS_SG_ID"

# $1 = desired task count for both services. Pass 1 deploys at 0: an ECS service with
# DesiredCount=0 reaches steady state immediately, so the stack completes BEFORE any image
# exists (CloudFormation otherwise blocks CREATE_COMPLETE until the service stabilizes, which
# never happens with an empty ECR repo -> multi-hour hang then rollback). Pass 2 deploys at 1
# once the images are pushed, so CloudFormation waits on a service that can actually start.
deploy_stack() {
  local desired="${1:-1}"
  ensure_artifacts_bucket
  log "Deploying $DEMO_STACK (image tag: $IMAGE_TAG, desired count: $desired)"
  aws cloudformation deploy \
    --region "$REGION" \
    --stack-name "$DEMO_STACK" \
    --template-file infra/g1/deploy.yaml \
    --s3-bucket "$CFN_ARTIFACTS_BUCKET" \
    --s3-prefix deploy-yaml \
    --capabilities CAPABILITY_IAM \
    --no-fail-on-empty-changeset \
    --parameter-overrides \
      VpcId="$VPC_ID" \
      PublicSubnetIds="$SUBNET_IDS" \
      RdsSecurityGroupId="$RDS_SG_ID" \
      DBEndpoint="$DB_ENDPOINT" \
      DBPort="$DB_PORT" \
      DBMasterSecretArn="$DB_SECRET_ARN" \
      DeepgramSecretArn="$DEEPGRAM_SECRET_ARN" \
      WorkerImageTag="$IMAGE_TAG" \
      BackendImageTag="$IMAGE_TAG" \
      ReportImageTag="$IMAGE_TAG" \
      GenerativeMode="${GENERATIVE_MODE:-0}" \
      DesiredWorkerCount="$desired" \
      DesiredBackendCount="$desired" \
      DesiredReportWorkerCount="$desired"
}

# ---- pass 1: create the stack + ECR repos with services at 0 (stabilizes immediately) --
if [[ "$DEPLOY_PHASE" == "all" || "$DEPLOY_PHASE" == "pass1" ]]; then
  deploy_stack 0

  WORKER_REPO="$(demo_out WorkerRepoUri)"
  BACKEND_REPO="$(demo_out BackendRepoUri)"
  REPORT_REPO="$(demo_out ReportWorkerRepoUri)"
  echo "WorkerRepo=$WORKER_REPO  BackendRepo=$BACKEND_REPO  ReportRepo=$REPORT_REPO"

  # ---- populate the Deepgram secret from .env (never committed) ------------------------
  log "Populating Deepgram secret from voice-worker/.env"
  DEEPGRAM_KEY="$(grep -E '^DEEPGRAM_API_KEY=' voice-worker/.env | head -1 | cut -d= -f2-)"
  if [[ -z "$DEEPGRAM_KEY" ]]; then
    echo "WARNING: DEEPGRAM_API_KEY not found in voice-worker/.env — worker STT/TTS will fail." >&2
  else
    aws secretsmanager put-secret-value --region "$REGION" \
      --secret-id "$DEEPGRAM_SECRET_ARN" \
      --secret-string "{\"DEEPGRAM_API_KEY\":\"${DEEPGRAM_KEY}\"}" >/dev/null
    echo "Deepgram secret updated."
  fi
fi

# ---- build + push images (linux/amd64; Fargate is x86_64). Only in DEPLOY_PHASE=all; on an
#      arm64 host (or no local Docker) use DEPLOY_PHASE=pass1, then build-images.sh, then
#      DEPLOY_PHASE=finish instead. ------------------------------------------------------
if [[ "$DEPLOY_PHASE" == "all" ]]; then
  log "ECR login"
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"

  log "Build + push voice-worker"
  docker build --platform linux/amd64 -t "${WORKER_REPO}:${IMAGE_TAG}" voice-worker/
  docker push "${WORKER_REPO}:${IMAGE_TAG}"

  log "Build + push backend"
  docker build --platform linux/amd64 -t "${BACKEND_REPO}:${IMAGE_TAG}" backend/
  docker push "${BACKEND_REPO}:${IMAGE_TAG}"

  log "Build + push report-worker"
  docker build --platform linux/amd64 -t "${REPORT_REPO}:${IMAGE_TAG}" report-worker/
  docker push "${REPORT_REPO}:${IMAGE_TAG}"
fi

if [[ "$DEPLOY_PHASE" == "pass1" ]]; then
  log "DEPLOY_PHASE=pass1 done — ECR repos created + Deepgram secret set."
  echo "Next:  infra/g1/build-images.sh ${IMAGE_TAG}   then   DEPLOY_PHASE=finish infra/g1/deploy.sh ${IMAGE_TAG}"
  exit 0
fi

# ---- pass 2: scale services to 1 now that images exist (CFN waits for steady state) ----
deploy_stack 1
log "Forcing new deployments so services pull the images"
aws ecs update-service --region "$REGION" --cluster "$DEMO_STACK" \
  --service "${DEMO_STACK}-voice-worker" --force-new-deployment >/dev/null
aws ecs update-service --region "$REGION" --cluster "$DEMO_STACK" \
  --service "${DEMO_STACK}-backend" --force-new-deployment >/dev/null
aws ecs update-service --region "$REGION" --cluster "$DEMO_STACK" \
  --service "${DEMO_STACK}-report-worker" --force-new-deployment >/dev/null

# ---- SPA: write runtime config, build, upload, invalidate ------------------------------
USER_POOL_ID="$(demo_out UserPoolId)"
CLIENT_ID="$(demo_out UserPoolClientId)"
SPA_BUCKET="$(demo_out SpaBucketName)"
CF_DOMAIN="$(demo_out CloudFrontDomain)"
DIST_ID="$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?DomainName=='${CF_DOMAIN}'].Id" --output text)"

log "Writing frontend runtime config (Cognito ids)"
cat > frontend/public/config.js <<EOF
// Generated by infra/g1/deploy.sh — DO NOT edit by hand for the hosted demo.
window.__APP_CONFIG__ = {
  cognitoRegion: "${REGION}",
  cognitoUserPoolId: "${USER_POOL_ID}",
  cognitoClientId: "${CLIENT_ID}",
};
EOF

log "Building SPA"
( cd frontend && npm ci --silent && npm run build )

log "Uploading SPA to s3://${SPA_BUCKET}"
# Hashed assets: long cache. index.html + config.js: no-cache so deploys take effect immediately.
aws s3 sync frontend/dist "s3://${SPA_BUCKET}" --delete \
  --exclude index.html --exclude config.js \
  --cache-control "public,max-age=31536000,immutable" --region "$REGION"
aws s3 cp frontend/dist/index.html "s3://${SPA_BUCKET}/index.html" \
  --cache-control "no-cache" --region "$REGION"
aws s3 cp frontend/dist/config.js "s3://${SPA_BUCKET}/config.js" \
  --cache-control "no-cache" --content-type "application/javascript" --region "$REGION"

log "Invalidating CloudFront"
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" >/dev/null

log "DONE"
echo "Demo URL:  https://${CF_DOMAIN}"
echo "Seed a demo user with:  infra/g1/seed-user.sh <email> <password>"
