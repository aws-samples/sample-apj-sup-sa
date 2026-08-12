# Claude Code on Amazon Bedrock AgentCore

## Problem

**Your laptop is not the best host for autonomous coding agents.**

Coding agents need a shell, a filesystem, the project checked out, and the right credentials. Your laptop has all of these — but it won the job by being the nearest machine, not the right one.

- **Close the lid, lose the work.** The agent dies mid-task.
- **Credentials live locally.** GitHub tokens and API keys sit exposed in your environment.
- **No persistence between sessions.** Every run starts from scratch.
- **No way to walk away.** You can't check on it from your phone or reconnect later.

## Solution

This sample moves the coding agent off your laptop and onto **Amazon Bedrock AgentCore Runtime** — a purpose-built host that gives every session a dedicated isolated Linux microVM with a persistent workspace, a real shell, and the surrounding system to make it production-ready:

- **Serverless execution** — Each session runs in an isolated microVM. No servers to manage, scales to zero, pay only for what you use.
- **Secure tool access via MCP Gateway** — GitHub credentials stored in Secrets Manager, accessed through an IAM-authenticated AgentCore Gateway. The coding agent never sees raw tokens.
- **Persistent shared storage** — S3 Files mounted at `/mnt/s3files` survives across sessions. Agents can build shared skills, save context, and collaborate.
- **Interactive WebSocket PTY** — Full terminal access to the running agent via `connect.py`. Watch Claude Code think, intervene, or just let it run autonomously.

---

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Your Terminal                                                                │
│                                                                              │
│  $ python connect.py --prompt "fix issue #1 and open a PR"                  │
│       │                                                                      │
│       │ WebSocket (SigV4-signed)                                            │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Amazon Bedrock AgentCore Runtime                                    │    │
│  │  (Isolated microVM — spins up on demand, scales to zero)            │    │
│  │                                                                      │    │
│  │  ┌──────────────────────────────────────────────────────────────┐   │    │
│  │  │  Claude Code                                                  │   │    │
│  │  │  • Reads/writes code                                         │   │    │
│  │  │  • Calls MCP tools for GitHub access                         │   │    │
│  │  │  • Uses Bedrock for inference (IAM role, no API keys)        │   │    │
│  │  └──────────┬───────────────────────────────────────────────────┘   │    │
│  │             │                                                        │    │
│  │             │ stdio → SigV4 HTTP (via /mnt/s3files/mcp/index.js)    │    │
│  │             ▼                                                        │    │
│  │  ┌──────────────────────────┐    ┌─────────────────────────────┐   │    │
│  │  │  AgentCore MCP Gateway   │    │  S3 Files (/mnt/s3files)    │   │    │
│  │  │  (IAM-authenticated)     │    │  • /mcp/ — Gateway proxy    │   │    │
│  │  │         │                │    │  • /skills/ — Shared skills  │   │    │
│  │  │         ▼                │    │  • /repos/ — Cloned repos    │   │    │
│  │  │  GitHub MCP Server       │    └─────────────────────────────┘   │    │
│  │  │  (Secrets Manager auth)  │                                       │    │
│  │  └──────────────────────────┘                                       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  Supporting Infrastructure:                                                  │
│  • VPC with private subnets + NAT (outbound internet for GitHub/npm)        │
│  • IAM roles (least-privilege, no static credentials anywhere)               │
│  • Secrets Manager (GitHub App private key + installation ID)                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Interactive PTY (WebSocket Shell)

Unlike typical serverless agents that only support request/response, this sample provides a **full interactive terminal** to the running microVM via WebSocket:

```
┌─────────────────────────────────────────────────────────────────┐
│ $ python connect.py                                              │
│                                                                  │
│ Connecting to AgentCore Runtime...                               │
│   Runtime: arn:aws:bedrock-agentcore:us-east-2:...:runtime/...  │
│   Session: a1b2c3d4-...                                         │
│                                                                  │
│ Connected! Launching Claude Code...                              │
│                                                                  │
│ ╭─────────────────────────────────────────────────────────────╮ │
│ │ Claude Code                                                  │ │
│ │                                                              │ │
│ │ > I'll list the open issues in your repo...                  │ │
│ │                                                              │ │
│ │ Tool: list_issues(owner="wirjo", repo="agentcore-demo-app") │ │
│ │ Result: 9 open issues found                                  │ │
│ │                                                              │ │
│ │ > Issue #1: "POST /tasks always assigns id=1"               │ │
│ │ > Let me read the code and fix this...                       │ │
│ │                                                              │ │
│ │ Tool: get_file(path="backend/app.py")                        │ │
│ │ Tool: create_branch(branch="fix/issue-1-task-id")           │ │
│ │ Tool: put_file(path="backend/app.py", ...)                  │ │
│ │ Tool: create_pull_request(title="Fix task ID increment")     │ │
│ │                                                              │ │
│ │ ✓ PR opened: github.com/wirjo/agentcore-demo-app/pull/10    │ │
│ ╰─────────────────────────────────────────────────────────────╯ │
└─────────────────────────────────────────────────────────────────┘
```

**How the PTY works:**

1. `connect.py` opens a SigV4-signed WebSocket connection to the AgentCore Runtime
2. AgentCore allocates (or reuses) a microVM and returns a shell session
3. The local terminal is put into raw mode — every keystroke is forwarded to the microVM
4. Shell output (stdout/stderr) streams back in real-time via WebSocket frames
5. The terminal is restored on disconnect

This means you can:
- **Watch Claude Code work** in real-time (great for demos)
- **Ctrl+C to interrupt** if it goes off track
- **Type additional instructions** mid-session
- **Reconnect** to the same session later (`--session <id>`)
- **Run headless** for CI/automation (`--prompt "..."`)

### MCP Gateway (Secure GitHub Access)

The coding agent accesses GitHub through a chain of secure components:

```
Claude Code → stdio proxy (index.js) → SigV4-signed HTTP → AgentCore Gateway → MCP Server → GitHub API
```

1. **MCP Proxy** (`/mnt/s3files/mcp/index.js`): A lightweight Node.js bridge that converts Claude Code's stdio MCP protocol into SigV4-signed HTTP requests to the AgentCore Gateway.

2. **AgentCore Gateway**: IAM-authenticated endpoint that routes MCP requests to the GitHub MCP Server runtime. Only callers with the correct IAM role can invoke it.

3. **GitHub MCP Server**: A FastMCP server running on its own AgentCore Runtime. Reads GitHub App credentials from Secrets Manager, mints installation tokens, and exposes tools like `list_issues`, `get_file`, `create_branch`, `put_file`, `create_pull_request`.

**Why this architecture?**
- The coding agent never has direct access to GitHub credentials
- All access is IAM-authenticated and auditable via CloudTrail
- You can revoke access instantly by updating the IAM policy
- The MCP server can be shared across multiple agents

### Persistent Storage (S3 Files)

Every session mounts the same S3 Files volume at `/mnt/s3files`:

```
/mnt/s3files/
├── mcp/              ← MCP Gateway Proxy (shared across all sessions)
│   ├── index.js
│   └── package.json
├── skills/           ← Shared skills (agents build and reuse these)
│   ├── python-review.md
│   └── security-audit.md
└── repos/            ← Cloned repositories (persisted between sessions)
    └── agentcore-demo-app/
```

This enables:
- **Session A** creates a skill → **Session B** uses it (no rebuild needed)
- Cloned repos persist between invocations (faster subsequent runs)
- Shared configuration and templates across all agent sessions

---

## Prerequisites

- AWS CLI v2 configured with valid credentials
- Docker with buildx support (for arm64 image builds)
- Python 3.10+ with `boto3`, `bedrock-agentcore`, and `websockets`
- `jq` and `gh` CLI installed
- A GitHub App (instructions below)
- Bedrock model access enabled for Claude Opus 4

## Quick Start

### Step 1: Deploy Shared Infrastructure (VPC + S3 Files)

```bash
cd coding_agents/infra
./setup.sh us-east-2
```

### Step 2: Create a GitHub App and Deploy the MCP Gateway

1. Create a GitHub App at [github.com/settings/apps](https://github.com/settings/apps):
   - Permissions: Contents (R/W), Issues (R/W), Pull Requests (R/W), Metadata (Read)
   - Webhook: disabled
   - Install on your target repo

2. Deploy:
```bash
cd gateway_mcp

export GITHUB_APP_ID="<your-app-id>"
export GITHUB_APP_PRIVATE_KEY_FILE="/path/to/private-key.pem"
export GITHUB_APP_INSTALLATION_ID="<your-installation-id>"
export AWS_REGION="us-east-2"

./deploy-all.sh
```

### Step 3: Build and Deploy Claude Code Runtime

```bash
cd coding_agents/claude-code
./setup.sh          # Build Docker image
python deploy.py    # Create AgentCore Runtime
```

### Step 4: Connect!

```bash
# Interactive (full PTY, see Claude Code work live)
python connect.py

# Headless one-shot (for CI or demos)
python connect.py --prompt "List open issues in wirjo/agentcore-demo-app and fix issue #1"
```

---

## Demo

### Setup a Demo Target Repository

```bash
# Create a repo with intentional bugs
gh repo create my-demo-app --private --clone
cp -r gateway_mcp/sample-project/* my-demo-app/
cd my-demo-app && git add . && git commit -m "Initial commit" && git push

# Seed 9 bug issues
cd ../gateway_mcp
./seed-issues.sh YOUR_USERNAME my-demo-app
```

### Run the Demo

```bash
cd coding_agents/claude-code
python connect.py --prompt "Use the GitHub MCP tools to list open issues in YOUR_USERNAME/my-demo-app, fix issue #1, and open a pull request"
```

**What happens:**
1. WebSocket connects → microVM starts (~5s cold start)
2. Claude Code discovers GitHub MCP tools
3. Lists issues → picks issue #1
4. Reads the buggy code via `get_file`
5. Creates a feature branch
6. Writes the fix via `put_file`
7. Opens a pull request

All serverless. No EC2. No long-running infrastructure.

---

## File Structure

```
├── README.md                        # This file
├── demo.sh                          # Single-command demo launcher
├── gateway_mcp/                     # GitHub MCP Gateway
│   ├── app/main.py                  # MCP server (FastMCP + GitHub API)
│   ├── sample-project/              # Buggy demo app for testing
│   ├── deploy-all.sh               # Deploy gateway + runtime + credentials
│   ├── seed-issues.sh              # Create demo issues in a repo
│   └── delete-all.sh              # Teardown
├── coding_agents/
│   ├── infra/                       # Shared infrastructure
│   │   ├── cfn-vpc.yaml            # VPC + private subnets + NAT
│   │   ├── setup.sh               # Deploy CloudFormation + S3 Files
│   │   └── cleanup.sh
│   ├── claude-code/                 # Claude Code runtime
│   │   ├── Dockerfile              # Container with Claude Code + git + AWS CLI
│   │   ├── run.sh                  # Entrypoint: configures MCP, launches claude
│   │   ├── connect.py             # Interactive PTY client (WebSocket)
│   │   ├── deploy.py             # Create/update AgentCore Runtime
│   │   ├── setup.sh              # Build and push Docker image
│   │   └── cleanup.py            # Delete runtime
│   └── requirements.txt
└── git_mcp_skill/                   # MCP Gateway Proxy
    ├── index.js                     # stdio → SigV4 HTTP bridge
    └── package.json
```

## Cleanup

```bash
cd coding_agents/claude-code && python cleanup.py   # Remove Claude Code runtime
cd coding_agents/infra && ./cleanup.sh              # Remove VPC + S3 Files
cd gateway_mcp && ./delete-all.sh                   # Remove MCP Gateway
```

## Security

| Layer | Mechanism |
|-------|-----------|
| Agent ↔ Runtime | SigV4-signed WebSocket (IAM authentication) |
| Agent ↔ MCP Gateway | SigV4-signed HTTP (IAM role, no credentials in container) |
| GitHub credentials | AWS Secrets Manager (encrypted, rotatable) |
| Network | Private subnets only, NAT for outbound, no inbound access |
| Session isolation | Each invocation = separate microVM |
| Model access | IAM role-based (no API keys) |

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
