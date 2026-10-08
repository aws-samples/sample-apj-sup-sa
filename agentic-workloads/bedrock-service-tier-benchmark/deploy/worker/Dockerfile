# Worker image: Strands discovery agent + bedrock_bench + Aurora loader. Build context = the sample root.
FROM public.ecr.aws/docker/library/python:3.13-slim
RUN useradd --create-home --uid 10001 bench
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
ENTRYPOINT ["python", "-m", "worker.main"]
