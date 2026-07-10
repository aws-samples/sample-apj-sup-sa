#!/usr/bin/env bash
# Phase C3 (cloud-build variant) — build the worker + backend images in AWS CodeBuild and push
# them to the ECR repos the demo stack created. Used because local Docker was unusable (host
# disk full -> corrupted Docker Desktop VM). Replaces the docker build/push lines in deploy.sh.
#
# Idempotent. Tears the transient build stack down at the end (keep --keep-stack to leave it up).
#
# Usage:  infra/g1/build-images.sh [IMAGE_TAG] [--keep-stack]
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
DEMO_STACK="interviewcoach-g1-demo"
BUILD_STACK="interviewcoach-g1-build"
IMAGE_TAG="c1"
KEEP_STACK=0
for arg in "$@"; do
  case "$arg" in
    --keep-stack) KEEP_STACK=1 ;;
    *) IMAGE_TAG="$arg" ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
log() { printf '\n=== %s ===\n' "$1"; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$REGION")"
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

demo_out() {
  aws cloudformation describe-stacks --stack-name "$DEMO_STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
build_out() {
  aws cloudformation describe-stacks --stack-name "$BUILD_STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

WORKER_REPO_URI="$(demo_out WorkerRepoUri)"
BACKEND_REPO_URI="$(demo_out BackendRepoUri)"
REPORT_REPO_URI="$(demo_out ReportWorkerRepoUri)"
WORKER_REPO_ARN="arn:aws:ecr:${REGION}:${ACCOUNT_ID}:repository/interviewcoach-g1/voice-worker"
BACKEND_REPO_ARN="arn:aws:ecr:${REGION}:${ACCOUNT_ID}:repository/interviewcoach-g1/backend"
REPORT_REPO_ARN="arn:aws:ecr:${REGION}:${ACCOUNT_ID}:repository/interviewcoach-g1/report-worker"
echo "WorkerRepo=$WORKER_REPO_URI  BackendRepo=$BACKEND_REPO_URI  ReportRepo=$REPORT_REPO_URI  tag=$IMAGE_TAG"

log "Deploying transient build stack $BUILD_STACK"
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$BUILD_STACK" \
  --template-file infra/g1/codebuild.yaml \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    WorkerRepoArn="$WORKER_REPO_ARN" \
    BackendRepoArn="$BACKEND_REPO_ARN" \
    ReportRepoArn="$REPORT_REPO_ARN" \
    WorkerRepoUri="$WORKER_REPO_URI" \
    BackendRepoUri="$BACKEND_REPO_URI" \
    ReportRepoUri="$REPORT_REPO_URI" \
    EcrRegistry="$ECR_REGISTRY" \
    ImageTag="$IMAGE_TAG"

SOURCE_BUCKET="$(build_out SourceBucketName)"
PROJECT_NAME="$(build_out ProjectName)"
echo "SourceBucket=$SOURCE_BUCKET  Project=$PROJECT_NAME"

# ---- package source: the two service dirs + the buildspec. git archive honours .gitignore, so
#      no .venv/node_modules/.env leak into the zip. CodeBuild unzips to the build root. ----
log "Packaging source.zip"
ZIP="$(mktemp -t g1src).zip"
git archive --format=zip -o "$ZIP" HEAD voice-worker backend report-worker infra/g1/codebuild-images.yml
aws s3 cp "$ZIP" "s3://${SOURCE_BUCKET}/source.zip" --region "$REGION"
rm -f "$ZIP"

# ---- run the build ----
log "Starting CodeBuild"
BUILD_ID="$(aws codebuild start-build --region "$REGION" --project-name "$PROJECT_NAME" \
  --query 'build.id' --output text)"
echo "BuildId=$BUILD_ID"

log "Waiting for build to finish (polling)"
while true; do
  STATUS="$(aws codebuild batch-get-builds --region "$REGION" --ids "$BUILD_ID" \
    --query 'builds[0].buildStatus' --output text)"
  PHASE="$(aws codebuild batch-get-builds --region "$REGION" --ids "$BUILD_ID" \
    --query 'builds[0].currentPhase' --output text)"
  echo "  status=$STATUS phase=$PHASE"
  case "$STATUS" in
    SUCCEEDED) echo "Build SUCCEEDED"; break ;;
    FAILED|FAULT|STOPPED|TIMED_OUT)
      echo "Build $STATUS — tail of log:" >&2
      LOG_GROUP="/aws/codebuild/${PROJECT_NAME}"
      STREAM="$(aws logs describe-log-streams --region "$REGION" --log-group-name "$LOG_GROUP" \
        --order-by LastEventTime --descending --max-items 1 \
        --query 'logStreams[0].logStreamName' --output text 2>/dev/null || true)"
      [[ -n "$STREAM" && "$STREAM" != "None" ]] && aws logs get-log-events --region "$REGION" \
        --log-group-name "$LOG_GROUP" --log-stream-name "$STREAM" \
        --query 'events[-40:].message' --output text >&2 || true
      exit 1 ;;
  esac
  sleep 12
done

# ---- verify the images are in ECR ----
log "Verifying images in ECR"
aws ecr describe-images --region "$REGION" --repository-name interviewcoach-g1/voice-worker \
  --image-ids imageTag="$IMAGE_TAG" --query 'imageDetails[0].imagePushedAt' --output text
aws ecr describe-images --region "$REGION" --repository-name interviewcoach-g1/backend \
  --image-ids imageTag="$IMAGE_TAG" --query 'imageDetails[0].imagePushedAt' --output text
aws ecr describe-images --region "$REGION" --repository-name interviewcoach-g1/report-worker \
  --image-ids imageTag="$IMAGE_TAG" --query 'imageDetails[0].imagePushedAt' --output text

if [[ "$KEEP_STACK" -eq 0 ]]; then
  log "Emptying + deleting transient build stack"
  aws s3 rm "s3://${SOURCE_BUCKET}" --recursive --region "$REGION" >/dev/null || true
  aws cloudformation delete-stack --stack-name "$BUILD_STACK" --region "$REGION"
  echo "Delete requested (not waited). Images remain in ECR."
fi

log "DONE — images pushed with tag $IMAGE_TAG"
