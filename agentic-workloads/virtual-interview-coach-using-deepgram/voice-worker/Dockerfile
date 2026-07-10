# Voice worker image (Pipecat library, Feature 007 — promoted to the canonical Dockerfile after the
# SC-001 gate PASSED live; the G1 hand-rolled-loop image is retired, rollback is the retained ECR tag).
#
# Python 3.13 base, non-root, port 8080, /health probe, `python -m src.server` entrypoint.
#
# Native stack: pipecat-ai[webrtc,deepgram,aws,silero] pulls aiortc/av/pylibsrtp (self-contained
# manylinux wheels), onnxruntime (Silero VAD + smart-turn ONNX models ship INSIDE the wheel — no
# runtime download), and opencv-python (via the webrtc extra). opencv needs libGL + libglib at
# runtime on slim Debian; ca-certificates is required for outbound TLS (Deepgram wss, AWS https).

ARG PYTHON_IMAGE=python:3.13-slim

# ---- builder: install the pinned Pipecat dependency set into a relocatable prefix ----
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=10

WORKDIR /build
COPY requirements-lock.txt .
RUN pip install --prefix=/install -r requirements-lock.txt

# ---- runtime: slim image with only the installed packages + app source ----
ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8080

# ca-certificates: outbound TLS to Deepgram (wss) and AWS (https).
# libgl1 + libglib2.0-0: runtime shared libs opencv-python (pulled by pipecat's webrtc extra) loads.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN useradd --create-home --uid 10001 worker
WORKDIR /app
COPY --chown=worker:worker src/ ./src/
USER worker

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
url='http://127.0.0.1:%s/health' % os.environ.get('SERVER_PORT','8080'); \
sys.exit(0 if urllib.request.urlopen(url, timeout=3).status==200 else 1)"

CMD ["python", "-m", "src.server"]
