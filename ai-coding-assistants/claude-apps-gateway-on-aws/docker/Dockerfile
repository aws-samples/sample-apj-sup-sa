FROM debian:bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --gid 0 --home-dir /tmp claude

COPY --chmod=0555 claude /usr/local/bin/claude
COPY --chmod=0555 render-gateway-config.sh /usr/local/bin/render-gateway-config.sh

USER 10001
EXPOSE 8080

ENV CLAUDE_CONFIG_DIR=/tmp/.claude
ENV CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
ENV GATEWAY_CONFIG_PATH=/tmp/claude-gateway/gateway.yaml

ENTRYPOINT ["/usr/local/bin/render-gateway-config.sh"]
