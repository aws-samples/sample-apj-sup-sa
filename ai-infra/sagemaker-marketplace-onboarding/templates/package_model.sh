#!/usr/bin/env bash
# Package model weights into model.tar for SageMaker Marketplace.
#
# The output goes to Marketplace-managed encrypted storage during
# CreateModelPackage. Customers never see the raw weights — SageMaker
# mounts them at /opt/ml/model/ inside the running container.
#
# Usage:  ./package_model.sh <weights-dir> <s3-uri>
# Example: ./package_model.sh ./model_artifacts s3://my-seller-bucket/my-model/model.tar

set -euo pipefail

WEIGHTS_DIR="${1:-}"
S3_URI="${2:-}"

if [[ -z "$WEIGHTS_DIR" || -z "$S3_URI" ]]; then
    echo "Usage: $0 <weights-dir> <s3-uri>"
    echo "  weights-dir : local directory containing weight files, tokenizer, config"
    echo "  s3-uri      : s3://<bucket>/<key> where model.tar will be uploaded"
    exit 1
fi

if [[ ! -d "$WEIGHTS_DIR" ]]; then
    echo "Weights directory $WEIGHTS_DIR does not exist"
    exit 1
fi

# Use UNCOMPRESSED tar. The spec warns that gzip decompression adds 1–3 minutes
# to cold start for large models — SageMaker has a hard 8-minute startup window.
echo "Packaging $WEIGHTS_DIR into model.tar (uncompressed)..."
tar -cf model.tar -C "$WEIGHTS_DIR" .

echo ""
echo "Contents of model.tar:"
tar -tf model.tar | head -50
echo ""

SIZE_BYTES=$(stat -f%z model.tar 2>/dev/null || stat -c%s model.tar)
SIZE_GB=$(awk "BEGIN {printf \"%.2f\", $SIZE_BYTES/1024/1024/1024}")
echo "model.tar size: ${SIZE_GB} GB"
echo ""

echo "Uploading to $S3_URI..."
aws s3 cp model.tar "$S3_URI"

echo ""
echo "Done. Reference this S3 URI as ModelDataUrl when creating your SageMaker Model:"
echo "  $S3_URI"
echo ""
echo "Reminder: after CreateModelPackage, weights are transferred to a Marketplace-"
echo "managed bucket and encrypted with a Marketplace KMS key. Keep a backup of the"
echo "source weights — you will lose direct access to the version you just uploaded."
