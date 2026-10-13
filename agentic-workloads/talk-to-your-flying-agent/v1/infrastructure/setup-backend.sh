#!/bin/bash
# Setup Terraform remote state backend (S3 + DynamoDB)
# Usage: ./setup-backend.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.dev"

# Optional local env file for AWS creds / backend overrides.
if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

REGION="${TF_STATE_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
STATE_KEY="${TF_STATE_KEY:-v1/infrastructure/terraform.tfstate}"
TABLE_NAME="${TF_STATE_LOCK_TABLE:-v1-tfstate-lock}"
BUCKET_PREFIX="${TF_STATE_BUCKET_PREFIX:-v1-tfstate}"

echo "Fetching AWS account ID..."
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="${BUCKET_PREFIX}-${AWS_ACCOUNT_ID}"

echo "Account: $AWS_ACCOUNT_ID"
echo "Region:  $REGION"
echo "Bucket:  $BUCKET_NAME"
echo "Table:   $TABLE_NAME"
echo "Key:     $STATE_KEY"
echo ""

if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
    echo "S3 bucket already exists"
else
    echo "Creating S3 bucket..."
    if [[ "$REGION" == "us-east-1" ]]; then
        aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION"
    else
        aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION" \
            --create-bucket-configuration LocationConstraint="$REGION"
    fi
    echo "Enabling versioning..."
    aws s3api put-bucket-versioning \
        --bucket "$BUCKET_NAME" \
        --versioning-configuration Status=Enabled
fi

if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$REGION" 2>/dev/null; then
    echo "DynamoDB table already exists"
else
    echo "Creating DynamoDB table..."
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION"

    echo "Waiting for table to be active..."
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$REGION"
fi

BACKEND_FILE="${SCRIPT_DIR}/backend.tfbackend"
echo "Writing $BACKEND_FILE..."
cat > "$BACKEND_FILE" <<EOF
bucket = "${BUCKET_NAME}"
key = "${STATE_KEY}"
region = "${REGION}"
dynamodb_table = "${TABLE_NAME}"
encrypt = true
EOF

echo ""
echo "Backend setup complete!"
echo ""
echo "Next steps:"
echo "  cd v1/infrastructure"
echo "  terraform init -backend-config=backend.tfbackend"
echo "  terraform plan"
echo "  terraform apply"
