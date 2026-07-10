# NemoClaw on AWS with Bedrock

[日本語版 / Japanese](README.ja.md)

Terraform module to deploy [NVIDIA NemoClaw](https://github.com/NVIDIA/NemoClaw) on an AWS EC2 instance, using [Amazon Bedrock](https://aws.amazon.com/bedrock/) as the LLM inference backend via a [LiteLLM](https://docs.litellm.ai/) proxy.

## Architecture

```
OpenClaw TUI (sandbox)
  → https://inference.local/v1 (OpenShell Gateway)
    → http://172.17.0.1:4000/v1 (LiteLLM proxy on host)
      → Bedrock API (ap-northeast-1)
```

NemoClaw does not natively support Bedrock, so LiteLLM acts as an OpenAI-compatible proxy that translates requests to the Bedrock API.

## Prerequisites

- Terraform >= 1.5
- AWS CLI configured with valid credentials
- [Session Manager Plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) installed (required for SSM connection)
- Bedrock model access enabled for the target model

## Deploy

```bash
terraform init
terraform apply
```

Wait 3–5 minutes after apply for User Data (Docker + LiteLLM + NemoClaw installation) to complete.

## Setup

### 1. Connect via SSM

```bash
# The connection command is shown in terraform output
aws ssm start-session --region ap-northeast-1 --target <instance-id>
```

### 2. Run NemoClaw onboard

```bash
sudo su - ec2-user
nemoclaw onboard
```

Select the following in the interactive wizard:

| Step | Selection |
|---|---|
| Inference options | `3) Other OpenAI-compatible endpoint` |
| Base URL | `http://172.17.0.1:4000/v1` |
| API key | `dummy` (no master_key configured on LiteLLM) |
| Model | `claude-opus` (matches `litellm_model_name` in `variables.tf`) |

### 3. Verify

After onboard completes:

```bash
nemoclaw my-assistant connect
openclaw tui
```

If the status bar shows `inference/claude-opus`, the setup is working.

## Dashboard

Access the NemoClaw dashboard locally via SSM port forwarding:

```bash
# Use the command shown in terraform output
aws ssm start-session --region ap-northeast-1 --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'
```

Open `http://127.0.0.1:18789` in your browser (use the token URL displayed after onboard).

## Variables

| Variable | Default | Description |
|---|---|---|
| `instance_type` | `m5.xlarge` | EC2 instance type (4 vCPU / 16 GB RAM) |
| `volume_size` | `40` | Root EBS volume size (GB) |
| `bedrock_model_id` | `global.anthropic.claude-opus-4-6-v1` | Bedrock model ID ([inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html) recommended) |
| `bedrock_region` | `ap-northeast-1` | AWS region for Bedrock API |
| `litellm_model_name` | `claude-opus` | Model alias used in NemoClaw onboard |
| `name_prefix` | `nemoclaw` | Prefix for resource names |

## Cleanup

```bash
terraform destroy
```

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
