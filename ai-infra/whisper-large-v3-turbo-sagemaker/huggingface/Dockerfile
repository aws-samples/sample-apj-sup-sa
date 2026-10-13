# Hugging Face Transformers inference image for openai/whisper-large-v3-turbo.
#
# Extends the AWS Hugging Face PyTorch inference DLC (CUDA 11.8 + PyTorch + the
# SageMaker inference toolkit) and bakes in:
#   - model weights at /opt/ml/model
#   - the custom handler at /opt/ml/model/code/inference.py
#
# With the model inside the image, SageMaker needs no model_data and pulls
# everything from ECR. Every instance (including autoscaled ones) reuses this
# image with no Hugging Face download and no pip install at startup.
#
# Build context is the REPO ROOT. Build for linux/amd64 (SageMaker hosts are x86_64).

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Bake the model weights into the standard model directory.
COPY model_artifacts/snapshot/ /opt/ml/model/

# Bake the custom inference handler.
COPY huggingface/inference.py /opt/ml/model/code/inference.py

# Tell the SageMaker Hugging Face toolkit where the handler lives.
# Run as non-root for container security hardening.
RUN useradd -m -s /bin/bash model && \
    chown -R model:model /opt/ml
USER model

ENV SAGEMAKER_PROGRAM=inference.py \
    SAGEMAKER_SUBMIT_DIRECTORY=/opt/ml/model/code \
    HF_TASK=automatic-speech-recognition \
    SAGEMAKER_MODEL_SERVER_TIMEOUT=600 \
    SAGEMAKER_TS_RESPONSE_TIMEOUT=600
