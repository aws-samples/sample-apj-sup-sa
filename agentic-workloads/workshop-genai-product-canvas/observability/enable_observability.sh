#!/usr/bin/env bash
# One-time per account/region: enable CloudWatch Transaction Search so AgentCore
# Observability traces (OTEL spans, token usage) are ingested and searchable.
#
# Docs: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
echo "==> Region: ${REGION}"

# 1. Let X-Ray ingest OTEL spans emitted by the agent runtime.
echo "==> Configuring X-Ray trace segment destination -> CloudWatch Logs"
aws xray update-trace-segment-destination \
  --destination CloudWatchLogs \
  --region "${REGION}" || {
    echo "    (If this command is unavailable, enable Transaction Search in the"
    echo "     CloudWatch console: CloudWatch -> Settings/Transaction Search -> Enable.)"
  }

# 2. Set the indexing rule so a useful percentage of traces is indexed.
echo "==> Setting Transaction Search indexing rule (100% sampling for the workshop)"
aws xray update-indexing-rule \
  --name "Default" \
  --rule '{"Probabilistic": {"DesiredSamplingPercentage": 100}}' \
  --region "${REGION}" 2>/dev/null || \
  echo "    (Indexing rule step is optional; skipping if not supported by your CLI.)"

echo ""
echo "==> Transaction Search setup attempted."
echo "    View agent telemetry: CloudWatch console -> GenAI Observability ->"
echo "    Bedrock AgentCore -> biodiversity-anomaly-agent"
