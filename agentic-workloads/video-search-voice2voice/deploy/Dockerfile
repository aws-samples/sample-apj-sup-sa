# Talk2Vid runtime image (built by CodeBuild on ARM64, run on Fargate ARM64).
FROM python:3.12-slim

# ffmpeg is a hard runtime dependency, not just an ingest one: the look_closer
# tool extracts real frames from the video on every call.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN pip install --no-cache-dir uv==0.9.7

# Dependencies first so they cache across code-only changes.
COPY pyproject.toml uv.lock ./
COPY talk2vid ./talk2vid
COPY client ./client
RUN uv sync --frozen --no-dev

COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Indexes and media are synced from S3 at start-up, not baked in.
RUN mkdir -p /app/videos /app/data/index /app/data/cache

EXPOSE 7860
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
