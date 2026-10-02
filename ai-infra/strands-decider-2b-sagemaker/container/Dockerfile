# Serving image for Strands Decider 2B on SageMaker AI.
# Base: the AWS PyTorch 2.9 Deep Learning Container (CUDA 13, kernels for T4, A10G, L4 and L40S).
# The AWS PyTorch *inference* images stop at torch 2.6, and strands-decider needs 2.7 or later.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/ml/model/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1

# Ubuntu security updates for the base image's OS packages (curl, openssl, glib, expat, libxml2 and others had
# CRITICAL and HIGH CVEs in ECR's scan of the unpatched base). Kernel headers stay: Triton compiles a small C
# launcher at run time.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --no-install-recommends \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Exact versions. The build fails if they add a dependency conflict the base image did not
# already have (the base ships one of its own, aiobotocore vs botocore, which this leaves alone).
COPY requirements.txt /opt/program/requirements.txt
# The base image also carries packages a serving container does not use; three have unfixed or fixed-upstream
# HIGH/CRITICAL CVEs (ffmpeg bundled in opencv-python, flash-attn, the SageMaker Python SDK), so they are removed.
RUN (pip check > /tmp/check.before || true) \
 && pip uninstall -y --root-user-action=ignore opencv-python opencv-python-headless flash-attn sagemaker \
 && pip install --no-cache-dir --root-user-action=ignore -r /opt/program/requirements.txt \
 && (pip check > /tmp/check.after || true) \
 && if grep -v -x -F -f /tmp/check.before /tmp/check.after; then echo "new dependency conflicts" && exit 1; fi \
 && python -c "import strands_decider, transformers, peft, fla; print(transformers.__version__, peft.__version__)"

COPY serve.py /opt/program/serve.py
WORKDIR /opt/program
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=4)"
LABEL com.amazonaws.sagemaker.capabilities.accept-bind-to-port=true

# SageMaker runs model containers as root ("SageMaker AI expects all containers to run with root
# users", docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-inference-code.html), so no USER.
# checkov:skip=CKV_DOCKER_3: SageMaker requires the container to run as root
# SageMaker starts the container as `docker run <image> serve`; the argument is ignored.
ENTRYPOINT ["python", "/opt/program/serve.py"]
