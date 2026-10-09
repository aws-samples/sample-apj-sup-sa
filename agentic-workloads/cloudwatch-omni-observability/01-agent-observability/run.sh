#!/usr/bin/env bash
# Start the support agent under the ADOT distribution, with the environment from
# "Send AI agent telemetry" (Amazon EC2 / Python) in the Omni docs. The agent
# exports traces straight to the regional CloudWatch (X-Ray) OTLP endpoint,
# signed with SigV4 using your current AWS credentials. No collector is needed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
if [ -f "$ROOT/.env" ]; then
  . "$ROOT/common/load-env.sh"
  omni_load_env "$ROOT/.env"
  unset -f omni_load_env
else echo "No $ROOT/.env. Create it first: cp $ROOT/.env.example $ROOT/.env (then edit)"; exit 1; fi
: "${AWS_REGION:?set AWS_REGION in .env}" "${MODEL_ID:?set MODEL_ID in .env}"

export AWS_DEFAULT_REGION="$AWS_REGION"
export AGENT_SERVICE_NAME="${AGENT_SERVICE_NAME:-support-agent}"

export AGENT_OBSERVABILITY_ENABLED=true
# Keep prompts, responses, and tool I/O on the spans. Agent traces and
# evaluators read them from there. For production redaction, see
# "Protect sensitive data" in the Omni docs.
export AWS_GENAI_CONTENT_EXTRACTION_OPT_OUT=true
export OTEL_PYTHON_DISTRO=aws_distro
export OTEL_PYTHON_CONFIGURATOR=aws_configurator
export OTEL_RESOURCE_ATTRIBUTES="service.name=${AGENT_SERVICE_NAME},deployment.environment.name=demo"
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_TRACES_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=none
export OTEL_METRICS_EXPORTER=none
# Spans land in the default Transaction Search log group (aws/spans). To use your
# own log group, set OTEL_EXPORTER_OTLP_TRACES_HEADERS=x-aws-log-group=...,x-aws-log-stream=...
# and add the CloudWatch Logs resource policy that lets xray.amazonaws.com write to it.

cd "$HERE"
exec opentelemetry-instrument python run_agent.py "$@"
