# AgentCore Runtime container: a Python agent plus headless Blender.
# AgentCore Runtime runs linux/arm64 images; Ubuntu's blender package is built for arm64.
FROM public.ecr.aws/ubuntu/ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends blender python3 python3-numpy python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && blender --version | head -1

# Generated scripts run as this unprivileged user, never as the agent's user.
RUN useradd --create-home --shell /usr/sbin/nologin sandbox \
 && useradd --create-home --shell /usr/sbin/nologin agent

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY *.py ./

EXPOSE 8080
# The agent stays root only so it can drop privileges (setuid) for the Blender subprocess.
CMD ["/opt/venv/bin/python", "app.py"]
