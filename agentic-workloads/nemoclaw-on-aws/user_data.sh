#!/bin/bash
set -euxo pipefail

# System packages
yum install -y docker git python3-pip perl-Digest-SHA
systemctl enable --now docker
usermod -aG docker ec2-user

# LiteLLM config
mkdir -p /opt/litellm/config
cat > /opt/litellm/config/config.yaml << 'LITELLM'
model_list:
  - model_name: "${litellm_model_name}"
    litellm_params:
      model: "bedrock/${bedrock_model_id}"
      aws_region_name: "${bedrock_region}"
LITELLM

# LiteLLM
sudo -u ec2-user pip3 install --user 'litellm[proxy]'

# LiteLLM systemd service
cat > /etc/systemd/system/litellm.service << 'SVC'
[Unit]
Description=LiteLLM Proxy
After=network.target docker.service

[Service]
User=ec2-user
Environment=PATH=/home/ec2-user/.local/bin:/usr/local/bin:/usr/bin
ExecStart=/home/ec2-user/.local/bin/litellm --config /opt/litellm/config/config.yaml --host 0.0.0.0 --port 4000
Restart=always

[Install]
WantedBy=multi-user.target
SVC

systemctl daemon-reload
systemctl enable --now litellm

# NemoClaw (install only, onboard is manual)
sudo -u ec2-user bash -c 'curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash -s -- --non-interactive --yes-i-accept-third-party-software' || true
