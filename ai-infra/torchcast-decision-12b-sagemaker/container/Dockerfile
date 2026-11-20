# Serving image for torchcast-decision-12b on SageMaker AI: the AWS vLLM Deep Learning Container (SageMaker
# flavour) plus TorchCast's decision server, behind SageMaker's /ping and /invocations routes. vLLM listens on
# loopback only; the decision server's port is the only one SageMaker reaches.
# build.sh passes the base image for your Region. Either way the tag is a versioned AWS DLC release (Checkov cannot
# read ARG defaults, hence the suppression).
ARG BASE_IMAGE=763104351884.dkr.ecr.us-east-2.amazonaws.com/vllm:0.30.0-gpu-py312-cu130-ubuntu24.04-sagemaker-v1.4
# checkov:skip=CKV_DOCKER_7: BASE_IMAGE is always a versioned AWS DLC tag
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_USAGE_STATS=1
# serving/ holds decision_server.py, torchcast_shim.py, readout_config.json and their MIT licence, downloaded by
# build.sh from TorchCast's repository at the v1.0.0 commit and checked against SHA-256 sums.
COPY serving/ /opt/program/serving/
COPY serve.py /opt/program/
# Unprivileged serving user. UID 1000 is the base image's ubuntu user, so this one takes 1001. It needs a writable
# home for the Hugging Face download and vLLM's compile cache, and read access to /opt/ml/model, which SageMaker
# mounts readable by all users.
RUN python3 -c "import vllm; print(vllm.__version__)" \
 && useradd --create-home --uid 1001 --shell /usr/sbin/nologin decider
ENV HOME=/home/decider HF_HOME=/home/decider/hf
USER decider
WORKDIR /opt/program
EXPOSE 8080
# vLLM downloads 24 GB of weights and compiles before /ping turns healthy, hence the long start period.
HEALTHCHECK --interval=30s --timeout=5s --start-period=900s \
  CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/ping' % os.environ.get('SAGEMAKER_BIND_TO_PORT', '8080'), timeout=4)"
LABEL com.amazonaws.sagemaker.capabilities.accept-bind-to-port=true

# SageMaker starts the container as `docker run <image> serve`; the argument is ignored.
ENTRYPOINT ["python3", "/opt/program/serve.py"]
