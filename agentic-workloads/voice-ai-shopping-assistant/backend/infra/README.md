# infra — AWS CDK (TypeScript) app

CDK v2. **One stack per silo** so deploys don't overlap; cross-stack values pass
through **SSM Parameter Store** (`/aisle/*`), never hard refs, so each silo deploys
independently.

```
infra/
  bin/aisle.ts            # app wiring (Agent 5)
  lib/data-stack.ts       # Agent 1 — Aurora SV2 + Secret + seed runner
  lib/tools-stack.ts      # Agent 2 — tool Lambdas + AgentCore Gateway
  lib/agent-stack.ts      # Agent 3 — ECR image + AgentCore Runtime + Memory
  lib/api-stack.ts        # Agent 5 — Session Broker Lambda + Function URL
  lib/web-stack.ts        # Agent 5 shell + Agent 4 bundle — S3 + CloudFront (OAC)
```

| Stack | Exports (SSM) | Consumes |
|---|---|---|
| `DataStack` | `db/cluster_arn`, `db/secret_arn`, `db/name` | — |
| `ToolsStack` | `gateway/mcp_url` | db params |
| `AgentStack` | `agent/runtime_arn`, `agent/memory_id` | gateway url |
| `ApiStack` | `session/url` | runtime arn |
| `WebStack` | `web/url` | session url (build-time) |

Deploy order: `DataStack → ToolsStack → AgentStack → ApiStack → WebStack`.
