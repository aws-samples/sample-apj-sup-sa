#!/usr/bin/env bash
set -euo pipefail

: "${NODE_RANK:?NODE_RANK is required}"
: "${WORLD_SIZE:?WORLD_SIZE is required}"
: "${MASTER_ADDR:?MASTER_ADDR is required}"
: "${MASTER_PORT:?MASTER_PORT is required}"
: "${HF_TOKEN:?HF_TOKEN is required}"

START_TS="$(date -u +%s)"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/gr00t-output}"
GPU_METRICS_DIR="/tmp/gr00t-gpu-metrics"
GPU_METRICS_CSV="${GPU_METRICS_DIR}/nvidia-smi.csv"
mkdir -p "${OUTPUT_DIR}" "${GPU_METRICS_DIR}"

log() {
  printf '[rank %s] %s\n' "${NODE_RANK}" "$*"
}

stop_gpu_metrics() {
  if [[ -n "${GPU_METRICS_PID:-}" ]]; then
    kill "${GPU_METRICS_PID}" >/dev/null 2>&1 || true
    wait "${GPU_METRICS_PID}" >/dev/null 2>&1 || true
    GPU_METRICS_PID=""
  fi
}
trap 'stop_gpu_metrics' EXIT

(
  set +e
  while true; do
    nvidia-smi \
      --query-gpu=timestamp,index,name,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,temperature.gpu \
      --format=csv,noheader,nounits >>"${GPU_METRICS_CSV}" 2>/dev/null || true
    sleep "${GPU_METRICS_INTERVAL_SECONDS:-10}"
  done
) &
GPU_METRICS_PID="$!"

export DEBIAN_FRONTEND=noninteractive
export HF_HOME=/tmp/hf-home
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
export PYTHONUNBUFFERED=1
export UV_LINK_MODE=copy
export UV_PROJECT_ENVIRONMENT=/tmp/gr00t-venv
export WANDB_DISABLED=true
: "${SHARD_SIZE:=1024}"
: "${EPISODE_SAMPLING_RATE:=0.1}"
: "${NUM_SHARDS_PER_EPOCH:=100000}"
: "${RETAIN_MODEL_WEIGHTS:=false}"

export FI_PROVIDER="${FI_PROVIDER:-efa}"
export FI_EFA_USE_DEVICE_RDMA="${FI_EFA_USE_DEVICE_RDMA:-1}"
export NCCL_DEBUG="${NCCL_DEBUG:-INFO}"
export NCCL_DEBUG_SUBSYS="${NCCL_DEBUG_SUBSYS:-INIT,NET}"
export LD_LIBRARY_PATH="/opt/amazon/efa/lib:/opt/amazon/ofi-nccl/lib/x86_64-linux-gnu:/opt/aws-ofi-nccl/lib:${LD_LIBRARY_PATH:-}"

log "node rank ${NODE_RANK}/${WORLD_SIZE}"
nvidia-smi
python --version

apt-get update >/dev/null
apt-get install -y --no-install-recommends \
  ca-certificates curl ffmpeg git git-lfs libgl1 libglib2.0-0 libstdc++6 >/dev/null
git lfs install --skip-repo >/dev/null
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
export PATH="${HOME}/.local/bin:${PATH}"

GIT_LFS_SKIP_SMUDGE=1 git clone --filter=blob:none --no-checkout "${GR00T_REPOSITORY}" /tmp/Isaac-GR00T
cd /tmp/Isaac-GR00T
git fetch --depth 1 origin "${GR00T_REF}"
git checkout "${GR00T_REF}"
git apply /workspace/isaac-gr00t-video-backend-env.patch
git apply /workspace/isaac-gr00t-video-indices-pyav-fallback.patch
git apply /workspace/isaac-gr00t-phase-timing.patch
git lfs pull --include "${DATASET_PATH}/**" --exclude ""
uv sync --frozen --no-dev

PYTHON_EXEC="${UV_PROJECT_ENVIRONMENT}/bin/python"
mkdir -p "${OUTPUT_DIR}/rank-${NODE_RANK}"

GR00T_VIDEO_BACKEND_REQUESTED="${GR00T_VIDEO_BACKEND:-auto}"
VIDEO_BACKEND_PROBE_LOG="${OUTPUT_DIR}/rank-${NODE_RANK}/video-backend-probe.log"
if [[ "${GR00T_VIDEO_BACKEND_REQUESTED}" == "auto" ]]; then
  if "${PYTHON_EXEC}" - <<'PY' >"${VIDEO_BACKEND_PROBE_LOG}" 2>&1; then
import json
import subprocess
import tempfile
from pathlib import Path

import torch
import torchcodec
from torchcodec.decoders import VideoDecoder

with tempfile.TemporaryDirectory() as tmpdir:
    output = Path(tmpdir) / "torchcodec-smoke.mp4"
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x96:rate=5:duration=2",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(output),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    decoder = VideoDecoder(
        str(output), device="cpu", dimension_order="NHWC", num_ffmpeg_threads=0
    )
    frames = decoder.get_frames_at(indices=[0, 1, 2]).data
    print(
        json.dumps(
            {
                "torch": torch.__version__,
                "torchcodec": getattr(torchcodec, "__version__", "unknown"),
                "frames_shape": tuple(frames.shape),
                "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True)
                .splitlines()[0],
            },
            sort_keys=True,
        )
    )
PY
    export GR00T_VIDEO_BACKEND=torchcodec
  else
    echo "torchcodec probe failed; using pyav indexed-frame fallback" >>"${VIDEO_BACKEND_PROBE_LOG}"
    export GR00T_VIDEO_BACKEND=pyav
  fi
else
  export GR00T_VIDEO_BACKEND="${GR00T_VIDEO_BACKEND_REQUESTED}"
  printf 'GR00T_VIDEO_BACKEND forced to %s\n' "${GR00T_VIDEO_BACKEND}" >"${VIDEO_BACKEND_PROBE_LOG}"
fi
log "GR00T video backend selected: ${GR00T_VIDEO_BACKEND}"
cat "${VIDEO_BACKEND_PROBE_LOG}"

"${PYTHON_EXEC}" - <<'PY' >"${OUTPUT_DIR}/rank-${NODE_RANK}/runtime-env.json"
import importlib.util
import json
import os
import subprocess
from importlib import metadata

payload = {}
for name in (
    "torch",
    "torchvision",
    "torchcodec",
    "av",
    "flash_attn",
    "deepspeed",
    "triton",
):
    spec = importlib.util.find_spec(name)
    payload[name] = {
        "present": spec is not None,
        "origin": getattr(spec, "origin", None) if spec is not None else None,
    }
    if spec is not None:
        try:
            payload[name]["version"] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
try:
    import torch

    payload["torch"]["version"] = torch.__version__
    payload["torch"]["cuda"] = torch.version.cuda
    if hasattr(torch.cuda, "nccl"):
        payload["torch"]["nccl"] = torch.cuda.nccl.version()
except Exception as exc:  # pragma: no cover - runtime diagnostics only
    payload["torch_error"] = repr(exc)
payload["gr00t_video_backend"] = os.environ.get("GR00T_VIDEO_BACKEND")
try:
    payload["ffmpeg"] = subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0]
except Exception as exc:  # pragma: no cover - runtime diagnostics only
    payload["ffmpeg_error"] = repr(exc)
print(json.dumps(payload, indent=2, sort_keys=True))
PY
cat "${OUTPUT_DIR}/rank-${NODE_RANK}/runtime-env.json"

set +e
"${PYTHON_EXEC}" -m torch.distributed.run \
  --nnodes="${WORLD_SIZE}" \
  --nproc_per_node="${GPUS_PER_NODE:-1}" \
  --node_rank="${NODE_RANK}" \
  --master_addr="${MASTER_ADDR}" \
  --master_port="${MASTER_PORT}" \
  gr00t/experiment/launch_finetune.py \
  --base-model-path "${BASE_MODEL_PATH}" \
  --dataset-path "${DATASET_PATH}" \
  --embodiment-tag "${EMBODIMENT_TAG}" \
  --modality-config-path "${MODALITY_CONFIG_PATH}" \
  --num-gpus "${WORLD_SIZE}" \
  --global-batch-size "${GLOBAL_BATCH_SIZE}" \
  --max-steps "${MAX_STEPS}" \
  --save-steps "${SAVE_STEPS}" \
  --save-total-limit "${SAVE_TOTAL_LIMIT}" \
  --output-dir "${OUTPUT_DIR}" \
  --experiment-name "${RUN_NAME}" \
  --dataloader-num-workers "${DATALOADER_NUM_WORKERS}" \
  --learning-rate "${LEARNING_RATE}" \
  --shard-size "${SHARD_SIZE}" \
  --episode-sampling-rate "${EPISODE_SAMPLING_RATE}" \
  --num-shards-per-epoch "${NUM_SHARDS_PER_EPOCH}" \
  --no-use-wandb
TRAIN_EXIT=$?
set -e

stop_gpu_metrics
END_TS="$(date -u +%s)"
mkdir -p "${OUTPUT_DIR}/rank-${NODE_RANK}/gpu-metrics"
cp "${GPU_METRICS_CSV}" "${OUTPUT_DIR}/rank-${NODE_RANK}/gpu-metrics/nvidia-smi.csv" 2>/dev/null || true
cp "${OUTPUT_DIR}/${RUN_NAME}/phase-timing-rank-${NODE_RANK}.json" \
  "${OUTPUT_DIR}/rank-${NODE_RANK}/phase-timing.json" 2>/dev/null || true
if [[ "${RETAIN_MODEL_WEIGHTS}" != "true" ]]; then
  find "${OUTPUT_DIR}" -type d -name "checkpoint-*" -prune -exec rm -rf {} +
  find "${OUTPUT_DIR}" -type f \( \
    -name "*.safetensors" -o \
    -name "*.pt" -o \
    -name "*.bin" -o \
    -name "pytorch_model*" -o \
    -name "optimizer.pt" -o \
    -name "scheduler.pt" -o \
    -name "rng_state.pth" \
  \) -delete
fi

cat >"${OUTPUT_DIR}/run-manifest-rank-${NODE_RANK}.json" <<JSON
{
  "example": "gr00t-so100-efa-multinode-finetune",
  "run_name": "${RUN_NAME}",
  "node_rank": ${NODE_RANK},
  "world_size": ${WORLD_SIZE},
  "gpus_per_node": ${GPUS_PER_NODE:-1},
  "repository": "${GR00T_REPOSITORY}",
  "ref": "${GR00T_REF}",
  "image": "${IMAGE_NAME:-unknown}",
  "install_mode": "locked",
  "node_selector_key": "${NODE_SELECTOR_KEY:-unknown}",
  "node_selector_value": "${NODE_SELECTOR_VALUE:-unknown}",
  "fi_provider": "${FI_PROVIDER:-unknown}",
  "fi_efa_use_device_rdma": "${FI_EFA_USE_DEVICE_RDMA:-unknown}",
  "nccl_net": "${NCCL_NET:-default}",
  "gr00t_video_backend": "${GR00T_VIDEO_BACKEND:-unknown}",
  "dataset_path": "${DATASET_PATH}",
  "base_model_path": "${BASE_MODEL_PATH}",
  "embodiment_tag": "${EMBODIMENT_TAG}",
  "modality_config_path": "${MODALITY_CONFIG_PATH}",
  "max_steps": ${MAX_STEPS},
  "global_batch_size": ${GLOBAL_BATCH_SIZE},
  "shard_size": ${SHARD_SIZE},
  "episode_sampling_rate": ${EPISODE_SAMPLING_RATE},
  "num_shards_per_epoch": ${NUM_SHARDS_PER_EPOCH},
  "gpu_metrics_interval_seconds": ${GPU_METRICS_INTERVAL_SECONDS:-10},
  "runtime_seconds": $((END_TS - START_TS)),
  "retain_model_weights": "${RETAIN_MODEL_WEIGHTS}",
  "train_exit": ${TRAIN_EXIT}
}
JSON

find "${OUTPUT_DIR}" -maxdepth 6 -type f | sort | head -300
if [[ "${TRAIN_EXIT}" -eq 0 ]]; then
  touch "${OUTPUT_DIR}/rank-${NODE_RANK}/train-ok"
  echo "GR00T_EFA_MULTINODE_OK rank=${NODE_RANK}"
  if [[ "${HOLD_FOR_ARTIFACT_COPY:-true}" == "true" ]]; then
    echo "GR00T_HOLD_FOR_ARTIFACT_COPY rank=${NODE_RANK}"
    while [[ ! -f "${OUTPUT_DIR}/rank-${NODE_RANK}/release" ]]; do
      sleep 5
    done
  fi
fi
exit "${TRAIN_EXIT}"
