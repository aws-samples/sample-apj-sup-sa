# buildingo.py sandbox image.
#
# Minimal base: only what the orchestrator and tools need unconditionally.
# Language toolchains (Python, Node, Rust, Go, etc.) are installed by the
# agents at runtime via `apt-get install` or language-specific installers.
# This keeps the image small and language-agnostic.

FROM debian:stable-slim@sha256:5012d0517aa0075a7150a45aae67586641e898913b7af3b08228108565b5f90c

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        git \
        gnupg \
        openssh-client \
        build-essential \
        pkg-config \
        make \
        jq \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Non-root user with passwordless sudo for package installation.
RUN useradd -m -s /bin/bash buildingo \
    && echo "buildingo ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/buildingo \
    && chmod 0440 /etc/sudoers.d/buildingo

RUN mkdir -p /work/workspace /work/spec \
    && chown -R buildingo:buildingo /work

WORKDIR /work/workspace

USER buildingo

RUN git config --global init.templateDir ""

CMD ["sleep", "infinity"]
