# Backend app API (Phase B2) — G1 session lifecycle + Cognito auth glue + voice_token mint.
#
# Pure-Python deps (fastapi, uvicorn, python-jose[cryptography], asyncpg, httpx, boto3) ship as
# wheels, so no system build toolchain is needed at runtime. python:3.13-slim matches the worker.

# Base image parameterized so CI pulls from AWS's ECR Public mirror (no Docker Hub 429 rate limit);
# byte-identical to the Hub image. Default keeps local `docker build` working with no extra args.
ARG PYTHON_IMAGE=python:3.13-slim

# ---- builder: install deps into a relocatable prefix ----
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=10

WORKDIR /build
COPY requirements.txt .
# Install only the runtime deps (skip the dev/test tooling block).
RUN sed '/# Dev \/ test/,$d' requirements.txt > requirements-runtime.txt \
    && pip install --prefix=/install -r requirements-runtime.txt

# ---- runtime ----
# Redeclare the ARG: a global ARG goes out of scope after the first FROM.
ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN useradd --create-home --uid 10001 api
WORKDIR /app
COPY --chown=api:api src/ ./src/
USER api

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
url='http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'); \
sys.exit(0 if urllib.request.urlopen(url, timeout=3).status==200 else 1)"

# Production server: uvicorn, no --reload. Single worker (G1 demo scale); the media plane is the
# scaling concern, not this control-plane API.
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
