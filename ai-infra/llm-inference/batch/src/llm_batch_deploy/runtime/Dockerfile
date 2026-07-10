# syntax=docker/dockerfile:1
# -----------------------------------------------------------------------------
# Batch container for llm-batch-deploy.
#
# Starts vLLM as a background process, then runs our Python entrypoint that
# drives it with asyncio concurrency. See run.sh.
#
# Base: vLLM's official image (already has CUDA + vLLM + OpenAI API).
# We just add our Python runtime on top.
# -----------------------------------------------------------------------------
ARG VLLM_IMAGE=vllm/vllm-openai:latest
FROM ${VLLM_IMAGE}

# Python deps for the entrypoint (vLLM image ships with Python already).
# Minimal set — does NOT include pydantic etc. from the full package since
# we only use the runtime submodule.
RUN pip install --no-cache-dir \
    "aioboto3>=13.0" \
    "httpx>=0.27" \
    "tenacity>=8.3"

# Copy just the runtime submodule (not the whole package; no pydantic, no boto3).
# The entrypoint does not import data/ or deployer/ or anything else.
WORKDIR /app
COPY src/llm_batch_deploy/runtime /app/llm_batch_deploy/runtime
COPY src/llm_batch_deploy/__init__.py /app/llm_batch_deploy/__init__.py
# stub out data/ so 'from llm_batch_deploy import ...' doesn't fail at import
RUN mkdir -p /app/llm_batch_deploy/data /app/llm_batch_deploy/deployer /app/llm_batch_deploy/submitter /app/llm_batch_deploy/waiter \
    && touch /app/llm_batch_deploy/data/__init__.py \
             /app/llm_batch_deploy/deployer/__init__.py \
             /app/llm_batch_deploy/submitter/__init__.py \
             /app/llm_batch_deploy/waiter/__init__.py
ENV PYTHONPATH=/app

# Wrapper that starts vLLM + entrypoint.
COPY src/llm_batch_deploy/runtime/run.sh /app/run.sh
RUN chmod +x /app/run.sh

# Batch hands us env vars; the wrapper reads them.
ENTRYPOINT ["/app/run.sh"]
