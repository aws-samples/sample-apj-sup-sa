FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
# Pull the latest patched Debian packages on top of the base image.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
RUN useradd --uid 10001 --no-create-home appuser
USER 10001
EXPOSE 8080
CMD ["python", "calling_task.py"]
