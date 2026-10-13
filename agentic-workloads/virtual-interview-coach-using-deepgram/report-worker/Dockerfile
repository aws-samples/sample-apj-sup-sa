# F003 Report Worker — async feedback-report scoring, consumed from SQS (no HTTP server).
#
# Pure-Python deps (asyncpg, boto3, python-dotenv) ship as wheels, so no build toolchain at runtime.
# Mirrors the backend image; the only differences are no exposed port (it polls SQS, not an ALB) and
# the entrypoint runs the consume loop.

# Base image parameterized so CI pulls from AWS's ECR Public mirror (no Docker Hub 429 rate limit).
ARG PYTHON_IMAGE=python:3.13-slim

# ---- builder: install deps into a relocatable prefix ----
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=10

WORKDIR /build
COPY requirements.txt .
# Install only the runtime deps (skip the pytest dev/test tooling).
RUN grep -viE '^(pytest|pytest-asyncio)' requirements.txt > requirements-runtime.txt \
    && pip install --prefix=/install -r requirements-runtime.txt

# ---- runtime ----
ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN useradd --create-home --uid 10001 worker
WORKDIR /app
COPY --chown=worker:worker src/ ./src/
USER worker

# Long-running SQS consume loop (no --session arg). Restarted by ECS on crash; SQS redrive replays
# any in-flight message (idempotent via report_job UNIQUE(session_id)).
CMD ["python", "-m", "src.consume"]
