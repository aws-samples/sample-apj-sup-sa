# Bedrock AgentCore Runtime requires linux/arm64. The deployment command selects
# that platform so the Dockerfile remains portable and lintable.
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The shared state layer is copied in rather than duplicated, so the Lambdas and
# the agent cannot drift apart on how a checkpoint is written.
COPY common/ ./common/

# Copy every module rather than naming them individually. An explicit list went
# stale when orchestrator.py and extract.py were added, and the container failed
# at import time with ModuleNotFoundError after a clean build and push.
COPY *.py ./

# --- AWS Lambda Web Adapter (Phase 2 CDK: hvi:agentCompute=lambda) -----------
# When this image runs on Lambda (container-image function), the Web Adapter
# extension bridges the Lambda Runtime API to the uvicorn HTTP server below, so
# the SAME :8080 app serves both AgentCore (run directly) and Lambda unchanged.
# The adapter is a passive extension: harmless when the container runs under
# AgentCore (it simply never activates). The application code and the
# /ping + /invocations contract are untouched.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/ping

# AgentCore probes /ping and posts to /invocations on 8080.
EXPOSE 8080

RUN adduser --disabled-password --gecos "" --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
