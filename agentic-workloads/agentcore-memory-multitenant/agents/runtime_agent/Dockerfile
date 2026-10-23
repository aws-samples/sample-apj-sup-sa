# AgentCore Runtime container for the shopping-assistant agent (arm64, as
# AgentCore Runtime requires). Built by deploy/15_runtime.py from a build
# dir that also contains lambda_src/chat_core/chat_core.py.
FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py chat_core.py ./
RUN useradd --uid 10001 --no-create-home agent
USER agent
EXPOSE 8080
# ADOT auto-instrumentation: spans for every Bedrock Converse call (GenAI
# semantic conventions) and every Gateway HTTP call, exported to X-Ray /
# CloudWatch GenAI Observability (AgentCore Runtime supplies the endpoint
# settings; deploy/15_runtime.py sets the rest).
CMD ["opentelemetry-instrument", "python", "-u", "app.py"]
