#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Batch container entrypoint. Reads env vars set by the job definition:
#
#   HF_MODEL_ID            e.g. google/medgemma-27b-text-it
#   MODEL_ID               served model name (passed to driver + used as
#                          default 'model' field in requests)
#   TENSOR_PARALLEL_SIZE   default 1
#   DATA_PARALLEL_SIZE     default 1
#   PIPELINE_PARALLEL_SIZE default 1
#   MAX_MODEL_LEN          default 16384
#   GPU_MEMORY_UTILIZATION default 0.90
#   DTYPE                  default bfloat16
#   EXTRA_SERVE_FLAGS      optional, appended verbatim to vllm serve
#                          (e.g. "--kv-cache-dtype fp8")
#
# Plus, injected at task-start by ECS agent from Secrets Manager:
#
#   HF_TOKEN               read-only, gated-model access
#   HUGGING_FACE_HUB_TOKEN same value, different name for HF libs
#
# SECURITY: Do NOT echo or log HF_TOKEN / HUGGING_FACE_HUB_TOKEN. If you
# add debug output that prints `env`, pipe through `grep -v -E '^(HF_|HUGGING_FACE_)'`
# to filter them out. These tokens must never land in CloudWatch Logs.
#
# Plus, passed per-job via SubmitJob containerOverrides.environment:
#
#   MANIFEST_S3_URI
#   OUTPUT_PREFIX_S3_URI
#   IN_FLIGHT_PER_JOB
#   OVERWRITE
#
# Starts vLLM in background, then runs the Python driver. When the driver
# exits, kill vLLM and exit with the driver's exit code.
# -----------------------------------------------------------------------------
set -euo pipefail

# Flush Python stdout/stderr line-by-line so CloudWatch sees container progress.
export PYTHONUNBUFFERED=1

# Ensure a writable /tmp — Batch/ECS sometimes starts containers with /tmp
# in a state where Python's tempfile.gettempdir() can't create files.
# (dill, which torch imports, probes tmp at module-import time — if tmp
# is unwritable, the whole 'import torch' chain explodes before vLLM can
# even start.)
mkdir -p /tmp /var/tmp
chmod 1777 /tmp /var/tmp
export TMPDIR=/tmp

# Dump a quick diagnostic block so any future breakage like this is
# debuggable from CloudWatch without SSM'ing into the instance.
echo "[run.sh] env check:"
echo "  whoami: $(whoami)"
echo "  pwd:    $(pwd)"
echo "  /tmp:   $(ls -ld /tmp | awk '{print $1, $3, $4}')"
echo "  TMPDIR: ${TMPDIR:-<unset>}"
df -h /tmp 2>&1 | tail -1 | awk '{print "  /tmp df:", $2, "total,", $4, "avail"}'
echo "  python3 tempfile check:"
python3 -c "import tempfile, sys; d = tempfile.gettempdir(); print(f'    tempfile.gettempdir() -> {d}')" \
  || { echo "  FAIL: tempfile probe failed; aborting before vLLM start"; exit 2; }

: "${HF_MODEL_ID:?HF_MODEL_ID must be set in the job definition}"
: "${MODEL_ID:?MODEL_ID must be set in the job definition}"
: "${MANIFEST_S3_URI:?MANIFEST_S3_URI must be set via SubmitJob overrides}"
: "${OUTPUT_PREFIX_S3_URI:?OUTPUT_PREFIX_S3_URI must be set via SubmitJob overrides}"

TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"
DATA_PARALLEL_SIZE="${DATA_PARALLEL_SIZE:-1}"
PIPELINE_PARALLEL_SIZE="${PIPELINE_PARALLEL_SIZE:-1}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-16384}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
DTYPE="${DTYPE:-bfloat16}"
IN_FLIGHT_PER_JOB="${IN_FLIGHT_PER_JOB:-32}"
OVERWRITE="${OVERWRITE:-false}"
ENABLE_PREFIX_CACHING="${ENABLE_PREFIX_CACHING:-true}"
EXTRA_SERVE_FLAGS="${EXTRA_SERVE_FLAGS:-}"
VLLM_STARTUP_TIMEOUT_S="${VLLM_STARTUP_TIMEOUT_S:-900}"
REQUEST_TIMEOUT_S="${REQUEST_TIMEOUT_S:-120}"

# Plan-author-provided extra env vars are exported into the JobDef as their
# own Environment entries by cfn_batch.job_definition(); they are already in
# the container's environ. Echo their NAMES (not values — values may include
# tokens, though the validator forbids reserved names) so CloudWatch logs
# show which model-specific knobs are active.
EXTRA_ENV_NAMES=""
for _name in VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8 VLLM_ATTENTION_BACKEND \
              VLLM_USE_V1; do
  if [[ -n "${!_name:-}" ]]; then
    EXTRA_ENV_NAMES+="${_name}=${!_name} "
  fi
done

echo "[run.sh] Starting vLLM:"
echo "  HF_MODEL_ID=${HF_MODEL_ID}"
echo "  served name=${MODEL_ID}"
echo "  TP=${TENSOR_PARALLEL_SIZE} DP=${DATA_PARALLEL_SIZE} PP=${PIPELINE_PARALLEL_SIZE}"
echo "  max_model_len=${MAX_MODEL_LEN} gpu_mem_util=${GPU_MEMORY_UTILIZATION} dtype=${DTYPE}"
echo "  enable_prefix_caching=${ENABLE_PREFIX_CACHING}"
echo "  extra_serve_flags=${EXTRA_SERVE_FLAGS:-<none>}"
echo "  extra_env=${EXTRA_ENV_NAMES:-<none>}"

# Re-parse EXTRA_SERVE_FLAGS through bash quoting rules so values like
# `'{"image":4}'` survive as a single argument. Plain unquoted ${VAR}
# expansion only word-splits on $IFS — it does NOT honor embedded quotes,
# so vLLM would otherwise see literal `'{"image":4}'` (with the single
# quotes still attached) and json.loads() would reject it.
declare -a EXTRA_SERVE_FLAGS_ARR=()
if [[ -n "${EXTRA_SERVE_FLAGS}" ]]; then
  # `eval` re-parses the string with full bash quoting; safe here because
  # plan authors control these strings (CFN params; no per-request input).
  eval "EXTRA_SERVE_FLAGS_ARR=(${EXTRA_SERVE_FLAGS})"
fi

# Launch vLLM in the background. Writes to /tmp/vllm.log so PID tracking is
# clean. A separate `tail -F` sidecar streams the log to stdout prefixed
# with [vllm] so it lands in CloudWatch via the awslogs driver.
python3 -m vllm.entrypoints.openai.api_server \
  --model "${HF_MODEL_ID}" \
  --served-model-name "${MODEL_ID}" \
  --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
  --data-parallel-size "${DATA_PARALLEL_SIZE}" \
  --pipeline-parallel-size "${PIPELINE_PARALLEL_SIZE}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
  --dtype "${DTYPE}" \
  --host 127.0.0.1 \
  --port 8000 \
  $( [[ "${ENABLE_PREFIX_CACHING,,}" == "true" ]] && echo "--enable-prefix-caching" ) \
  "${EXTRA_SERVE_FLAGS_ARR[@]}" \
  > /tmp/vllm.log 2>&1 &

VLLM_PID=$!
echo "[run.sh] vLLM PID=${VLLM_PID}; logs at /tmp/vllm.log (also tee'd to stdout)"

# Stream vLLM log to stdout so CloudWatch captures it. `tail -F` retries
# if the file doesn't exist yet.
(
  # Wait briefly for the log file to appear.
  for _ in 1 2 3 4 5; do
    [[ -f /tmp/vllm.log ]] && break
    sleep 1
  done
  tail -n +1 -F /tmp/vllm.log 2>/dev/null | sed 's/^/[vllm] /'
) &
TAIL_PID=$!

# Teardown handler — on any exit, kill vLLM + tail sidecar.
cleanup() {
  echo "[run.sh] cleanup: SIGTERM to vLLM (pid=${VLLM_PID}) + tail (pid=${TAIL_PID})"
  if kill -0 "${VLLM_PID}" 2>/dev/null; then
    kill -TERM "${VLLM_PID}" || true
    # Give it a moment before SIGKILL.
    for _ in 1 2 3 4 5; do
      kill -0 "${VLLM_PID}" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "${VLLM_PID}" 2>/dev/null || true
  fi
  # Kill the log-tail sidecar too.
  kill -TERM "${TAIL_PID}" 2>/dev/null || true
}
trap cleanup EXIT

# Run the driver. process_shard waits for vLLM readiness itself.
python3 -m llm_batch_deploy.runtime.entrypoint \
  --manifest-s3-uri "${MANIFEST_S3_URI}" \
  --output-prefix-s3-uri "${OUTPUT_PREFIX_S3_URI}" \
  --vllm-base-url "http://127.0.0.1:8000" \
  --in-flight "${IN_FLIGHT_PER_JOB}" \
  --model-id "${MODEL_ID}" \
  --vllm-startup-timeout-s "${VLLM_STARTUP_TIMEOUT_S}" \
  --request-timeout-s "${REQUEST_TIMEOUT_S}" \
  $( [[ "${OVERWRITE,,}" == "true" ]] && echo "--overwrite" )

DRIVER_EXIT=$?
echo "[run.sh] Driver exit=${DRIVER_EXIT}"
exit "${DRIVER_EXIT}"
