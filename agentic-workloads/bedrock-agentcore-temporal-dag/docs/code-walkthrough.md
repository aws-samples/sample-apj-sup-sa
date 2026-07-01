# Code Walkthrough

## Repository Structure

```
workflow/              Temporal Worker (DAG execution engine)
  main.py             Worker entrypoint
  demo.py             Demo CLI with live progress display
  flows/              Workflow definitions (DAG flows)
  activities.py       Activity definitions (Agent invocation units)
  a2a_client.py       A2A protocol communication layer

agents/               Individual Agents (each runs on AgentCore Runtime)
  gather/main.py      Information gathering Agent
  analyze/main.py     Analysis Agent
  evaluate/main.py    Evaluation Agent
  synthesize/main.py  Synthesis Agent

cdk/                  CDK infrastructure definitions
```

## Orchestrator

### main.py — Worker entrypoint

```python
worker = Worker(
    client,
    task_queue="daf-orchestrator",
    workflows=[ResearchPipelineWorkflow],
    activities=[invoke_agent],
)
await worker.run()
```

The Temporal Worker uses a poll-based model. It connects to Temporal Cloud and pulls tasks from the `daf-orchestrator` queue. No inbound ports are required — only outbound traffic is needed, making it a natural fit for ECS Fargate.

### flows/research_pipeline.py — DAG flow definition

This file is the core of the system. The DAG is expressed directly as Python code.

```
gather → [analyze, evaluate] → (conditional: re_analyze if score < 0.7) → synthesize
```

**Design highlights:**

- `@workflow.defn` + `@workflow.run`: Registers the class as a Temporal Workflow. Temporal persists the execution state of this code.
- `workflow.execute_activity("invoke_agent", args=[...])`: Invokes an Agent as an Activity. On failure, Temporal schedules retries automatically.
- `asyncio.gather(...)`: Expresses fan-out. Runs analyze and evaluate in parallel.
- `if score < 0.7`: Conditional branch. Re-analysis runs only when the evaluation score is below threshold.
- `@workflow.query def get_status()`: Provides a query API to inspect workflow status while it is running.

**RetryPolicy:**

Each Activity has an explicit retry policy. `maximum_attempts=3, backoff_coefficient=2.0` means up to 3 retries with exponential backoff: 2s → 4s → 8s.

### activities.py — Activity definitions

```python
@activity.defn
async def invoke_agent(agent_name: str, input_data: dict) -> dict:
    return await _invoke_agent(agent_name, input_data)
```

A thin wrapper. The `@activity.defn` decorator registers the function as a Temporal Activity. Actual communication is delegated to `a2a_client.py`.

### a2a_client.py — A2A communication layer

Handles all communication with Agents. Two execution paths exist: local mode and AWS mode.

**Local mode** (when `AGENT_ENDPOINTS` env var is set):

1. Reads Agent URLs from the environment variable (e.g. `{"gather":"http://localhost:9001",...}`)
2. Resolves the Agent Card via a2a-sdk's `A2ACardResolver` → `ClientFactory`
3. Sends an A2A protocol message via `SendMessageRequest`
4. Extracts text from the task artifacts in the response

**AWS mode** (default):

1. **Service discovery**: Fetches the Agent ARN from SSM Parameter Store
2. **URL construction**: Builds the AgentCore Runtime invoke URL from the ARN
   - `https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{URL_ENCODED_ARN}/invocations`
3. **SigV4 signing**: Signs the request with AWS credentials (service: `bedrock-agentcore`)
4. **JSON-RPC POST**: Sends the A2A protocol payload (JSON-RPC 2.0) directly
5. **Response parsing**: Extracts text from artifacts or status message and returns it as JSON

In AWS mode, the a2a-sdk's `A2ACardResolver` / `ClientFactory` are not used. AgentCore Runtime exposes only a single invoke endpoint and does not support `GET /.well-known/agent-card.json`.

**SigV4HTTPXAuth class:**

A custom httpx Auth implementation. Automatically attaches AWS SigV4 signatures to each request, signing as the `bedrock-agentcore` service and including the session ID header.

**Caching:**

`_arn_cache` caches SSM call results in process memory. Since the Worker is long-lived and calls the same Agents repeatedly, this reduces per-invocation latency.

### demo.py — Demo CLI

Submits a workflow to Temporal Cloud and polls the Query API to display live status in the terminal.

```bash
python demo.py "Investigate multi-agent design patterns for generative AI"
```

- Generates a Workflow ID and calls `start_workflow`
- Calls `handle.query("get_status")` every 2 seconds and redraws the terminal progress
- Displays the final result via `handle.result()` after completion

## Agents

### Agent structure (example: gather/main.py)

```python
_model = BedrockModel(
    model_id=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

agent = Agent(
    model=_model,
    system_prompt="...",
    tools=[web_search, extract_content],
)

a2a_server = A2AServer(agent=agent, http_url=runtime_url, serve_at_root=True)
app = FastAPI()
app.get("/ping")(ping)
app.mount("/", a2a_server.to_fastapi_app())
```

**Design highlights:**

- `@tool` decorator: Defines tools for the LLM via the Strands SDK. Currently stub implementations — replace with SerpAPI or similar for production.
- `A2AServer`: Strands SDK's A2A server. Exposes the Agent as an A2A-protocol-compatible endpoint.
- `/ping`: Health check endpoint required by the AgentCore Runtime contract.
- `PORT` env var: Allows each Agent to run on a different port for local development (default: 9000).
- `MODEL_ID` env var: Makes the model configurable per deployment target.
- `Dockerfile` `ARG AGENT_NAME`: A single Dockerfile builds all four Agent images using this build argument.

## CDK Infrastructure

### network_stack.py

Creates a VPC: 2 AZs, 1 NAT Gateway, public and private subnets. The Orchestrator (ECS) runs in the private subnet and communicates externally via the NAT Gateway.

### agents_stack.py

- ECR Repository x4: Container image storage for each Agent
- SSM Parameter x4: Service discovery for AgentCore Runtime ARNs (initial values are placeholders)
- IAM Role (AgentCore Execution Role):
  - Bedrock model invocation (`foundation-model` + `inference-profile`)
  - ECR image pull (`GetAuthorizationToken`, `BatchGetImage`, `GetDownloadUrlForLayer`)

### orchestrator_stack.py

- ECS Cluster + Fargate Service: Execution environment for the Temporal Worker
- Task Definition: 256 CPU / 512 MB RAM, ARM64. Temporal connection details injected via environment variables; API key retrieved from Secrets Manager.
- Security Group: Outbound-only (no inbound ports needed — Worker is poll-based)
- Capacity Provider: Fargate Spot preferred (`weight=1`), minimum 1 On-Demand task (`base=1`). Spot interruptions are safe because Temporal automatically reschedules in-progress tasks.
