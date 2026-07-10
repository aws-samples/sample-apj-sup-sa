# vLLM-based inference image for openai/whisper-large-v3-turbo.
#
# Extends the official vLLM OpenAI server image and bakes in the model weights and
# a small SageMaker contract proxy. vLLM provides continuous (in-flight) batching,
# so concurrent transcription requests are merged into batched GPU passes -- the
# key to far higher throughput than the single-worker HF pipeline.
#
# CUDA COMPATIBILITY (important): newer vLLM images use CUDA 12.8/12.9 which need
# an NVIDIA driver (>=565/575) newer than SageMaker's g5 hosting AMI provides --
# those images fail with CannotStartContainerError before emitting any logs.
# v0.8.5.post1 uses CUDA 12.4 (driver >=550), which the SageMaker host satisfies.
# Re-check host driver compatibility before bumping this tag.
#
# Build context is the REPO ROOT. Build for linux/amd64. Only COPY/ENTRYPOINT --
# no RUN -- so cross-building from arm64 needs no emulation.

ARG BASE_IMAGE=vllm/vllm-openai:v0.8.5.post1
FROM ${BASE_IMAGE}

# Bake the model weights (HF format) into the image.
COPY model_artifacts/snapshot/ /opt/ml/model/snapshot/

# SageMaker contract proxy + Python launcher.
COPY vllm/proxy.py /opt/ml/proxy.py
COPY vllm/launch.py /opt/ml/launch.py

# Run as non-root for container security hardening.
RUN useradd -m -s /bin/bash model && \
    chown -R model:model /opt/ml
USER model

# Use python3 (guaranteed present) as the entrypoint to avoid shell exec issues.
ENTRYPOINT ["python3", "/opt/ml/launch.py"]
