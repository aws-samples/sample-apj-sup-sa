FROM public.ecr.aws/docker/library/python:3.12-slim
#checkov:skip=CKV_DOCKER_2:Health checks are service-specific in Compose; the shared loadgen image has no HTTP endpoint.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && opentelemetry-bootstrap -a install
COPY shop ./shop
COPY loadgen.py .
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
# Each service picks its module through the compose `command`, for example:
#   opentelemetry-instrument uvicorn shop.orders:app --host 0.0.0.0 --port 8000
