FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

ARG AGENT_NAME
COPY ${AGENT_NAME}/ ./agent/

CMD ["python", "agent/main.py"]
