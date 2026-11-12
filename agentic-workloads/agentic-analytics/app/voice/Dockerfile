# Pipecat Cloud agent image for the Voice Analytics bot.
# Built on Daily's Pipecat base image (provides the PCC runtime + /start handling).
# Pipecat Cloud pulls and runs this image on Daily's side, so it comes from Docker Hub
# (pinned tag) rather than ECR. The AgentCore voice mode uses Dockerfile.agentcore.
FROM dailyco/pipecat-base:0.2.10-py3.12

# Faster, smaller installs.
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies from the lockfile (no project install, no dev deps).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Application code. The bot's entrypoint is bot.py:bot(runner_args), which the
# Pipecat base image invokes per session. analytics_processor + auth are the
# AgentCore bridge; the presenter SOP is loaded from S3 at runtime (not bundled),
# so it does not need to be copied here.
COPY ./bot.py bot.py
COPY ./analytics_processor.py analytics_processor.py
COPY ./auth.py auth.py
