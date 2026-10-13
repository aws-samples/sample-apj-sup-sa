# MCP Server

The Model Context Protocol (MCP) server that runs on AgentCore Runtime.

## Tools

Defined in `main.py` using `FastMCP`:

- **`query_database`** — Run a SELECT-only SQL query against the internal Aurora database (destructive keywords blocked).
- **`list_documents`** — List objects in the internal S3 bucket.
- **`get_document`** — Fetch a single document's content from S3.
- **`ask_analyst`** — Delegate analysis to the A2A data-analyst agent (see [`../a2a-agent/`](../a2a-agent/)).

## Runtime configuration

The server discovers its backend resources (Aurora, S3, A2A Runtime) from SSM Parameter Store at startup:

- `/internalagent/CLUSTER_ARN`
- `/internalagent/SECRET_ARN`
- `/internalagent/DATABASE`
- `/internalagent/S3_BUCKET`
- `/internalagent/A2A_AGENT_ARN`

These parameters are populated by the CDK Data stack.

## Deployment

This directory is built as a container by [`../cdk/scripts/build-push.sh`](../cdk/scripts/build-push.sh)
and pushed to ECR as `internalagent/internal-mcp-server:latest`. The AgentCore Runtime is provisioned
by the `InternalAgent-AgentCore` CDK stack in [`../cdk/`](../cdk/).

See the top-level [README](../README.md) and [docs/workshop-guide.md](../docs/workshop-guide.md) for the full deployment flow.

## Local development

```bash
uv sync
uv run python main.py
```

The server starts on port 8000 with the MCP Streamable HTTP transport (as required by
the [AgentCore Runtime service contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html)).

Local runs will not talk to Aurora/S3/Bedrock unless valid AWS credentials with the right IAM permissions
are set via environment variables and the SSM parameters exist.

## Adding a new tool

Define it using the `@mcp.tool()` decorator in `main.py`:

```python
@mcp.tool()
def my_tool(param: str) -> str:
    """Short description shown to the LLM as the tool schema."""
    return f"Result: {param}"
```

Rebuild and push the container image, then update the Runtime version:

```bash
# from the repo root
cd cdk
./scripts/build-push.sh
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id <RuntimeId from CDK output> \
  ...  # see workshop-guide Step 9 / 10 for the full command
```
