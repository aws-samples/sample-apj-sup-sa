# Dockerfile — SageMaker Marketplace-compatible inference container.
#
# ==============================================================================
#  ONE IMAGE PER ENDPOINT
# ==============================================================================
# Marketplace expects exactly ONE Docker image per model listing. Multi-stage
# builds are fine (and encouraged for smaller final images), but only the FINAL
# stage's image gets pushed to ECR and referenced from CreateModel. Do NOT try
# to ship separate "loader" and "server" images — everything the container
# needs at inference time (weights loader, HTTP server, model runtime) lives
# in this one image.
#
# ==============================================================================
#  HOW SAGEMAKER STARTS YOUR CONTAINER — the ENTRYPOINT + CMD contract
# ==============================================================================
# SageMaker literally runs:
#
#     docker run <image> serve
#
# Docker resolves that to: whatever is in ENTRYPOINT + "serve" appended as
# argv. There are two supported patterns; pick ONE:
#
#   Pattern A — recommended for single-process containers:
#     ENTRYPOINT ["python3", "app.py"]
#     CMD ["serve"]
#
#     What happens: docker exec's `python3 app.py serve`. "serve" lands in
#     sys.argv[1]. app.py can inspect it if you want dual train/serve modes.
#     Running locally with `docker run <image>` (no args) also works — CMD
#     provides "serve" as the default. `docker run <image> train` would
#     replace "serve" with "train" for training use cases.
#
#   Pattern B — multi-process containers (see supervisord.conf):
#     ENTRYPOINT ["/usr/bin/supervisord"]
#     CMD ["-c", "/etc/supervisord.conf"]
#
#     Supervisord ignores the "serve" arg entirely, but that's fine — your
#     `[program:api-gateway]` block runs `python3 app.py serve` explicitly.
#
# ==============================================================================
#  WHY EXEC FORM (JSON ARRAY) IS REQUIRED
# ==============================================================================
# Exec form: ENTRYPOINT ["python3", "app.py"]   ✓
# Shell form: ENTRYPOINT python3 app.py         ✗ BROKEN
#
# Shell form makes Docker wrap the command in `/bin/sh -c "..."`. Signals sent
# to the container go to sh, which does NOT forward them to your Python
# process. When SageMaker sends SIGTERM during scale-down, your app never
# hears it, cannot flush buffers, cannot release GPU memory, and gets
# SIGKILL'd after 30s. This is one of the most common Marketplace validation
# failures — check your ENTRYPOINT form BEFORE anything else.
#
# ==============================================================================
#  OTHER RULES THE SPEC ENFORCES (do not violate any of these)
# ==============================================================================
#   1. Run as root. Do not add a USER directive. Non-root causes permission
#      issues with the /opt/ml/model/ mount.
#   2. Do NOT bundle NVIDIA drivers — SageMaker provides them.
#   3. Do NOT bake model weights into this image. Weights are packaged
#      separately as model.tar and mounted at /opt/ml/model/ at deploy time.
#   4. Do NOT use tini as init — it gets confused by the `serve` argument.
#   5. Expose port 8080 only. If you run multiple internal processes, use
#      supervisord and bind everything else to localhost.
#   6. Container has ZERO outbound network at inference time. Bundle every
#      dependency; no `pip install` at runtime, no HuggingFace Hub, no
#      external license servers.

# Use a CUDA runtime image matching the target instance's CUDA version.
# For g5/g6 (Ampere/Ada) — 12.1 is a safe default. Adjust for other families.
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04

# --- (Only if you also implement the WebSocket endpoint /invocations-bidirectional-stream) ---
# LABEL com.amazonaws.sagemaker.capabilities.bidirectional-streaming=true

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Belt-and-suspenders for HuggingFace ecosystems: hub calls will fail loudly
# instead of hanging on DNS timeout. Delete or ignore if not using HF libs.
ENV TRANSFORMERS_OFFLINE=1 \
    HF_HUB_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py model_loader.py inference.py /app/

# SageMaker mounts model.tar contents here at deploy time. Read-only.
# Do NOT COPY weights into the image.
# ENV SM_MODEL_DIR=/opt/ml/model   # (default already read by app.py)

EXPOSE 8080

# --- Single-process ENTRYPOINT (default) ---
# Exec-form ENTRYPOINT is REQUIRED. SageMaker will invoke:
#   docker run <image> serve
# The "serve" argument lands in sys.argv[1].
ENTRYPOINT ["python3", "app.py"]
CMD ["serve"]

# --- Multi-process ENTRYPOINT (uncomment + comment out the block above) ---
# Only if you need multiple internal processes (model server + API gateway,
# inference + metrics sidecar, etc.). See templates/supervisord.conf.
# Every program in supervisord.conf MUST pipe stdout to /dev/stdout with
# stdout_logfile_maxbytes=0 — otherwise program logs never reach CloudWatch.
#
# RUN apt-get update && apt-get install -y --no-install-recommends supervisor && \
#     rm -rf /var/lib/apt/lists/*
# COPY supervisord.conf /etc/supervisord.conf
# ENTRYPOINT ["/usr/bin/supervisord"]
# CMD ["-c", "/etc/supervisord.conf"]
