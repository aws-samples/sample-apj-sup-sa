# A2A Data Analyst Agent

An Agent-to-Agent (A2A) protocol server that analyses data with Amazon Bedrock (Claude).
Runs on AgentCore Runtime with `protocolConfiguration: A2A`.

## What it does

The MCP server's `ask_analyst` tool sends A2A JSON-RPC `message/send` requests to this
runtime. The `DataAnalystExecutor` extracts the user text, calls Bedrock's `converse`
API on `jp.anthropic.claude-sonnet-4-6`, and enqueues a text-only agent message back.

## Protocol implementation

Built with the official `a2a-sdk` (`>= 0.3, < 1.0`) via the
[`bedrock_agentcore.runtime.a2a.serve_a2a`](https://pypi.org/project/bedrock-agentcore/) helper:

- `DataAnalystExecutor` implements `a2a.server.agent_execution.AgentExecutor`
- Listens on port **9000** (the AgentCore A2A service-contract port)
- Exposes the standard A2A endpoints (`/`, `/.well-known/agent.json`, `/ping`)

See [the A2A tutorial](https://a2a-protocol.org/) for background on the protocol.

## Runtime configuration

| Env var | Default | Purpose |
|---|---|---|
| `AWS_REGION` | `ap-northeast-1` | Bedrock region |
| `MODEL_ID` | `jp.anthropic.claude-sonnet-4-6` | Bedrock model (must be an inference-profile ID for on-demand-restricted models) |
| `PORT` | `9000` | Override the A2A port |

## Deployment

Built by [`../cdk/scripts/build-push-a2a.sh`](../cdk/scripts/build-push-a2a.sh) and pushed as
`internalagent/data-analyst-agent:latest`. Provisioned by the `InternalAgent-AgentCore`
CDK stack.

## Local development

```bash
pip install "bedrock-agentcore[a2a]>=1.10.0" "a2a-sdk[http-server]>=0.3,<1.0" "boto3>=1.35.0"
python main.py
```

Then interact with the agent's A2A endpoint:

```bash
# health
curl http://localhost:9000/ping

# agent card
curl http://localhost:9000/.well-known/agent.json | jq

# invoke
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{"role":"user","messageId":"m1","parts":[{"kind":"text","text":"Analyse this: revenue Q1=100, Q2=150, Q3=120"}]}}}'
```

Local runs need valid AWS credentials with `bedrock:InvokeModel` permission for the target model.
