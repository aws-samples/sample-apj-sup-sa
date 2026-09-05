# Troubleshooting

## Local agent (Part 2, Steps 3)

**`ModuleNotFoundError: strands`**
Activate the venv and install requirements: `source .venv/bin/activate &&
pip install -r agent/requirements.txt`.

**`AccessDeniedException` / `You don't have access to the model`**
Bedrock model access for Claude Sonnet is not enabled in your region. Enable it in
the Bedrock console (Model access) or ask the facilitator. Confirm `AWS_REGION`
matches where access is granted.

**`ValidationException: model identifier is invalid`**
Set `BEDROCK_MODEL_ID` to a model ID available in your account/region. Default is
`us.anthropic.claude-sonnet-4-6` (a cross-region inference profile). List options:
`aws bedrock list-foundation-models --by-provider anthropic --query "modelSummaries[].modelId"`.

**The agent calls no tools / rambles**
Your `system_prompt.txt` lacks an ordered methodology, or tool descriptions are
vague. Tighten the methodology section and the tool descriptions in
`tool_definition.json` / `tools_local.py` docstrings.

## Remote tools (Step 4)

**`deploy_tools.sh` fails on bucket name already exists**
Bucket names are globally unique. The template scopes names by account + region,
so this usually means a leftover stack. Delete the old `biodiversity-anomaly-tools`
stack (empty the buckets first) and re-run.

**Lambda smoke test returns `Unknown tool`**
Expected. A direct `aws lambda invoke` does not carry the Gateway tool-name
context. The tool name only arrives via `context.client_context.custom
['bedrockAgentCoreToolName']` when called through the Gateway.

## Gateway (Step 5, gateway mode)

**`create_gateway.py`: `UnknownServiceError: bedrock-agentcore-control`**
Your boto3 is too old for AgentCore. Upgrade: `pip install -U boto3 botocore`.
Field names in the create calls may also shift between SDK versions — compare with
the current docs (linked in the script header) and adjust.

**`401 / invalid_token` when running in gateway mode**
The bearer token is missing or its scope is wrong. Re-`source gateway.env`. Check
`COGNITO_SCOPE` matches the resource server scope the gateway allows, and that the
M2M client is in the gateway's `allowedClients`.

**`403` from the Gateway to the Lambda**
The gateway IAM role cannot invoke the Lambda. Confirm
`biodiversity-anomaly-gateway-role` has `lambda:InvokeFunction` on the tools ARN
and that the function's resource policy allows `bedrock-agentcore.amazonaws.com`
(the `GatewayInvokePermission` in the template).

**Tools not discovered (`list_tools_sync` returns empty)**
The target may still be synchronizing, or the tool schema failed validation.
Re-check the target status; ensure `tool_definition.json` schemas are valid JSON
Schema.

## Deploy (Step 6)

The deploy step uses the AgentCore CLI (`@aws/agentcore`, a Node.js tool). The
older `bedrock-agentcore-starter-toolkit` pip CLI (`agentcore configure` /
`launch`) is deprecated; do not use it.

**`agentcore: command not found`**
Install the CLI: `npm install -g @aws/agentcore` (needs Node.js 20+). Verify with
`agentcore --help`.

**`agentcore deploy`: CDK bootstrap / CloudFormation errors**
The CLI deploys with AWS CDK. Bootstrap the account/region once: `cdk bootstrap`.
Use `agentcore deploy -v` for verbose output to find the failing resource, and
`agentcore deploy --dry-run` to preview.

**Deployed agent errors on tool calls but local worked**
The runtime is missing the Gateway env vars. Confirm `TOOL_MODE=gateway` and the
`COGNITO_*` / `AGENTCORE_GATEWAY_URL` values (from `gateway.env`) are set as the
agent's runtime environment in `agentcore/agentcore.json`, then re-run
`agentcore deploy` (see `content/040-part-2-design/050-build.md`).

**`RuntimeClientError: Runtime initialization time exceeded`**
Your entrypoint ran work at import/startup instead of starting the server. The
runtime expects the agent server to bind quickly. Ensure `agent.py`'s `__main__`
calls `app.run()` when invoked with no CLI args (as this repo does), not a full
investigation.

**`agentcore create` rejects the name**
Runtime/project names allow letters, digits, and underscores only, no hyphens. Use
`biodiversity_anomaly_agent`.

**A flag or config field differs from these notes**
The AgentCore CLI and its `agentcore/agentcore.json` schema evolve between
versions. Check `agentcore <command> --help` and the CLI docs for the version you
installed.

## Python version

The agent needs **Python 3.10+** (AgentCore/Strands requirement). If your system
Python is older (e.g. 3.9), create the venv with a newer interpreter, for example
with `uv`: `uv venv --python 3.12 .venv && source .venv/bin/activate`.

## Async batch (Step 7 / extension)

**Batch Lambda times out**
Six full investigations run serially exceed the Lambda timeout. This repo runs the
stations concurrently (ThreadPoolExecutor) and writes each station's result to S3
as it finishes, so progress survives. Keep the function timeout at 900s.

**`Read timeout on endpoint URL ... /invocations`**
The AgentCore data-plane client uses the default 60s read timeout, shorter than an
investigation. The invoker configures `botocore Config(read_timeout=840,
retries={"max_attempts": 0})`; keep that if you adapt the code.

**`Invalid length for parameter runtimeSessionId`**
`runtimeSessionId` must be 33-128 characters. The invoker appends a `uuid4().hex`
to guarantee the length.

## Observability (Step 7)

**No traces / empty GenAI dashboard**
Transaction Search is not enabled, or no invocations have run since enabling it.
Run `bash observability/enable_observability.sh`, then invoke the agent again and
wait 1-2 minutes. Confirm you are viewing the correct region.

**Token usage missing**
Ensure the agent was deployed via `agentcore deploy` (auto-instrumented) or that
`aws-opentelemetry-distro` is installed in the runtime environment.
