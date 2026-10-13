# infra — AWS CDK (TypeScript) app

CDK v2. **One stack per component** so deploys don't overlap; cross-stack values
pass through **SSM Parameter Store** (`/aisle/*`), never hard refs, so each stack
deploys independently.

```
infra/
  bin/aisle.ts            # CDK app wiring
  lib/data-stack.ts       # Aurora SV2 + Secret + seed runner
  lib/tools-stack.ts      # tool Lambdas + AgentCore Gateway
  lib/agent-stack.ts      # ECR image + AgentCore Runtime + Memory
  lib/api-stack.ts        # Session Broker Lambda + Function URL
  lib/web-stack.ts        # S3 + CloudFront (OAC)
```

| Stack | Exports (SSM) | Consumes |
|---|---|---|
| `DataStack` | `db/cluster_arn`, `db/secret_arn`, `db/name` | — |
| `ToolsStack` | `gateway/mcp_url` | db params |
| `AgentStack` | `agent/runtime_arn`, `agent/memory_id` | gateway url |
| `ApiStack` | `session/url` | runtime arn |
| `WebStack` | `web/url` | session url (build-time) |

Deploy order: `DataStack → ToolsStack → AgentStack → ApiStack → WebStack`.
