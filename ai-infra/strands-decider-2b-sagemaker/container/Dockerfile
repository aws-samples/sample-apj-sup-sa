# Serving image for Strands Decider 2B on SageMaker AI.
# Base: the AWS PyTorch 2.9 Deep Learning Container (CUDA 13, kernels for T4, A10G, L4 and L40S).
# The AWS PyTorch *inference* images stop at torch 2.6, and strands-decider needs 2.7 or later.
# The notebook passes the same image for its own Region; the default documents what is expected. Either way the
# tag is an explicit, versioned AWS DLC release (Checkov cannot read ARG defaults, hence the suppression).
ARG BASE_IMAGE=763104351884.dkr.ecr.us-east-1.amazonaws.com/pytorch-training:2.9.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v2.2
# checkov:skip=CKV_DOCKER_7: BASE_IMAGE is always a versioned AWS DLC tag (default above, pinned in the notebook)
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/ml/model/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1

# Ubuntu security updates for the base image's OS packages (curl, openssl, glib, expat, libxml2 and others had
# CRITICAL and HIGH CVEs in ECR's scan of the unpatched base). Kernel headers stay: Triton compiles a small C
# launcher at run time. The image tag is a hash of this directory, but the upgrade takes whatever Ubuntu has
# published on the build day, so two builds with the same tag can differ in OS package versions.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --no-install-recommends \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Exact versions, installed with --no-deps so nothing else is resolved. The build fails if they add a dependency
# conflict the base image did not already have (the base ships one of its own, aiobotocore vs botocore).
COPY requirements.txt /opt/program/requirements.txt
# The base image also carries packages a serving container does not use; three have unfixed or fixed-upstream
# HIGH/CRITICAL CVEs (ffmpeg bundled in opencv-python, flash-attn, the SageMaker Python SDK), so they are removed.
RUN (pip check > /tmp/check.before || true) \
 && pip uninstall -y --root-user-action=ignore opencv-python opencv-python-headless flash-attn sagemaker \
 && pip install --no-cache-dir --no-deps --root-user-action=ignore -r /opt/program/requirements.txt \
 && (pip check > /tmp/check.after || true) \
 && if grep -v -x -F -f /tmp/check.before /tmp/check.after | grep -v '^No broken requirements found'; then \
      echo "new dependency conflicts" && exit 1; fi \
 && python -c "import strands_decider, transformers, peft, fla, httpx, starlette, uvicorn; print(transformers.__version__, peft.__version__)"

COPY serve.py app.py /opt/program/
# Run as an unprivileged user. It needs a writable home for Triton's kernel cache and read access to
# /opt/ml/model, which SageMaker mounts readable by all users.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin decider
ENV HOME=/home/decider
USER decider
WORKDIR /opt/program
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/ping' % os.environ.get('SAGEMAKER_BIND_TO_PORT', '8080'), timeout=4)"
LABEL com.amazonaws.sagemaker.capabilities.accept-bind-to-port=true

# SageMaker starts the container as `docker run <image> serve`; the argument is ignored.
ENTRYPOINT ["python", "/opt/program/serve.py"]
