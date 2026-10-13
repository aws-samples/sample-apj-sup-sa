# Worker image: Strands discovery agent + bedrock_bench + Aurora loader. Build context = the sample root.
# checkov:skip=CKV_DOCKER_2: one-shot batch task run by ECS/EventBridge Scheduler; no long-running service to health-check
FROM public.ecr.aws/docker/library/python:3.13-slim
RUN useradd --create-home --uid 10001 bench && mkdir /work && chown bench:bench /work
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY deploy/worker/requirements.lock ./worker-requirements.lock
RUN pip install --no-cache-dir --require-hashes -r worker-requirements.lock
COPY pyproject.toml README.md ./
COPY bedrock_bench ./bedrock_bench
RUN pip install --no-cache-dir --no-deps .
COPY deploy/worker/*.py ./worker/
USER bench
# The root filesystem is read-only on Fargate; /work is the task's writable scratch volume.
ENV HOME=/work TMPDIR=/work
ENTRYPOINT ["python", "-m", "worker.main"]
