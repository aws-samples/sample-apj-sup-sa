FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv

RUN useradd -m -u 1000 bedrock_agentcore

RUN uv pip install --system "bedrock-agentcore[a2a]>=1.10.0" "a2a-sdk[http-server]>=0.3,<1.0" "boto3>=1.35.0"

COPY --chown=bedrock_agentcore:bedrock_agentcore . .

USER bedrock_agentcore

# AgentCore Runtime service contract ports
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html
# 8080: HTTP Mode
# 9000: A2A Mode
EXPOSE 8080 9000

CMD ["python", "main.py"]
