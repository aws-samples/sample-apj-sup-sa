# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Container image for the AgentCore Runtime Slack gateway agent.
#
# AgentCore Runtime executes on linux/arm64 and expects the container to serve
# HTTP on port 8080 with /invocations (POST) and /ping (GET). BedrockAgentCoreApp
# provides both endpoints; `app.run()` binds 0.0.0.0:8080.
#
# The target platform (linux/arm64) is supplied by the build (the CDK
# DockerImageAsset sets `--platform linux/arm64`; build manually with
# `docker buildx build --platform linux/arm64 ...`).
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so the layer is cached across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Agent source.
COPY runtime_agent.py config.py ./

# AgentCore Runtime contract: HTTP server on port 8080.
EXPOSE 8080

# opentelemetry-instrument enables auto-instrumentation/tracing, matching the
# CodeZip entrypoint. runtime_agent.py's __main__ guard calls app.run().
CMD ["opentelemetry-instrument", "python", "runtime_agent.py"]
