---
title: "Step 4: Deploy Prebaked SQL Toolset"
weight: 21
---

## Learning Objectives

By the end of this step, you will:
- Deploy the Prebaked SQL toolset (29 analytics tools) to the Gateway
- Understand how tools map to database Views — the LLM never generates SQL
- Understand how the deploy script registers tools to the Gateway and how the SOP guides tool selection

## Why Prebaked SQL?

This is the **first of three toolsets** you'll deploy. The Prebaked SQL pattern is the safest approach for analytics: your database team creates Views for common queries, each View maps to a tool, and the LLM's only job is to pick the right tool and extract arguments.

::alert[**Minimum hallucination risk.** The LLM never sees or generates SQL. It reads the tool description (e.g., `get_top_revenue_customers_tool: "Get top customers by revenue"`), decides whether to call it given the user's request, extracts the tool arguments from given information, and calls the tool. Your database team controls the SQL; the LLM controls the routing and information passing.]{type="success"}

## Lab Procedures

### Step 4.1: Uncomment the Prebaked SQL toolset and deploy

This step adds the AWS Lambda that implements the tools (database connection + queries) **and** registers it with the AgentCore Gateway as an MCP target — all by uncommenting one section of the top-up template.

Open :code[/workshop/agentic-analytics/app/agentcore_strands/agentcore-topup-stack.yaml]{showCopyAction=true} and find the fence:

```
# ===== UNCOMMENT FROM HERE (Step 4: Prebaked SQL toolset ...) =====
...
# ===== UNCOMMENT TO HERE (Step 4) =====
```

Delete the leading `# ` on every line **between** the two marker lines (this brings the `DataFoundationLambda`, its role + permission, the psycopg2 layer, and the `DataToolsTarget` to life).

::alert[**Tip — uncomment the whole block at once.** This block is large, so don't delete each `#` by hand. The Code Editor is VS Code: click the first line *inside* the fence, then **Shift+click** the last line inside it to select the whole block, and press **Cmd + /** (macOS) or **Ctrl + /** (Windows/Linux) to toggle the comments off for every selected line in one go. Select only the lines **between** the two `UNCOMMENT` markers — not the marker lines themselves.]{type="info"}

Then deploy:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make deploy
```

::::expand{header="💡 Not sure you uncommented it cleanly? Click to see the whole Step-4 block"}
The fenced block, once uncommented, defines `DataFoundationLambdaRole`, `DataFoundationLambda`, `DataFoundationLambdaPermission`, the conditional `Psycopg2Layer`, and the `DataToolsTarget` Gateway target. If `make deploy` errors with a YAML/indentation complaint, the safest fix is to copy the exact block from the workshop solution or re-clone the file — every line in the block is indented two spaces under `Resources:`.
::::

`make deploy` updates the stack in a couple of minutes. When it finishes, the Gateway has a new target named `PrebakedSQL` with 20+ tools.

### Step 4.2: Examine what you uncommented — Tool Schema and Gateway Registration

Let's understand what that block does. Still in :code[agentcore-topup-stack.yaml]{showCopyAction=true}, look at the `DataToolsTarget` resource you just uncommented.

**`ToolSchema.InlinePayload`:** This is the list of tools the Gateway advertises to the agent. Each entry has three fields:

```yaml
- Name: get_top_revenue_customers_tool
  Description: Get top customers by revenue
  InputSchema:
    Type: object
    Properties:
      limit: { Type: integer }
```

- `Name` — the tool identifier the LLM sees and calls
- `Description` — the LLM reads this to decide *when* to call the tool (this is the only thing guiding tool selection!). Provide enough detail to avoid confusion between similar tools.
- `InputSchema` — tells the LLM what arguments to extract from the user's question

**The Gateway target itself:** the resource registers that schema to the Gateway and points it at the Lambda:

```yaml
DataToolsTarget:
  Type: AWS::BedrockAgentCore::GatewayTarget
  Properties:
    GatewayIdentifier: !GetAtt Gateway.GatewayIdentifier
    Name: PrebakedSQL
    TargetConfiguration:
      Mcp:
        Lambda:
          LambdaArn: !GetAtt DataFoundationLambda.Arn
          ToolSchema:
            InlinePayload: [ ... the tool list above ... ]
```

The Gateway now knows: *"When the agent calls any tool in this `InlinePayload`, route it to this Lambda."* The target name `PrebakedSQL` becomes a prefix — the agent sees tools as `PrebakedSQL___get_top_revenue_customers_tool`.

::alert[**The tool description is your steering wheel.** If you write `"Get top customers by revenue"`, the LLM will call this tool when users ask about top customers. If the description is vague or wrong, the LLM picks the wrong tool. Good descriptions = accurate routing.]{type="info"}

### Step 4.3: Understand the SOP — Query Classification and Tool Selection

The agent's behavior is governed by a Standard Operating Procedure (SOP) — a markdown file that uses :link[RFC 2119]{href="https://www.rfc-editor.org/rfc/rfc2119" external=true} keywords (MUST, SHOULD, MAY) for precise behavior control.

Open :code[unicorn_rental_analytics.sop.md]{showCopyAction=true} and review these sections:

**Lines 18-36 — Query Classification:** How the agent categorizes each user question and decides which tool to call. Notice the constraint: *"You MUST identify if the query maps to a specific analytics tool"* — this prevents the agent from guessing.

**Lines 38-56 — Tool Selection and Tool Mapping Table:** The mapping from user intent to specific tool. For example, "Top customers" → `get_top_revenue_customers_tool`. This is how the agent knows to call the right prebaked tool instead of generating SQL.

**Lines 124-135 — Response Formatting:** Constraints like *"You MUST present data in clear, formatted tables"* and *"You MUST provide actionable insights."* This is why the agent returns structured tables with business insights, not raw data dumps (that you will try below)

**Lines 180-188 — Constraints Summary:** The hard rules — no emojis, no SQL in responses, use specific tools before falling back to text-to-sql.

::alert[**The SOP is the agent's playbook.** Without it, the LLM guesses which tool to call and how to format responses. With it, behavior is consistent, testable, and reviewable — like code, but in natural language. As you add more toolsets in the next steps, you'll see how the SOP guides the agent's behavior for each one.]{type="info"}

::alert[**Should I rebuild the agent?** Adding a toolset is often more than registering tools on the Gateway. You usually also add guidance in the SOP to help the agent route to the new tools, plus response-formatting rules. When you change the SOP (or any agent code), rebuild the image with `make build`. In our case the SOP already ships with the instructions for every tool, so no rebuild is needed here — uncommenting the target and `make deploy` is enough.]{type="info"}

### Step 4.4: Test — Ask Your First Analytics Question

Switch to the chat UI you set up in Step 3. If you're not logged in, log in as:

| Field | Value |
|-------|-------|
| Username | `stella.moonbeam@example-mythicalunicorns.com` |
| Password | `Unicorn123!` |

::alert[**Start fresh:** It is best to clear the chatbot conversation from the previous step by clicking the small bin icon next to the chat input field or by refreshing the application demo browser tab.]{type="info"}

In the chat, ask:

> "Show me top 5 customers by revenue"

The agent should now return a formatted table with customer names and revenue figures. This is the Prebaked SQL pattern in action — the LLM picked `PrebakedSQL___get_top_revenue_customers_tool` and passed `limit=5`.

### Step 4.5: Deep Exercise — Trace the "Top Customers" Query

Let's verify exactly what happened under the hood.

#### Part A — Check the trace in CloudWatch

1. If you are accessing AWS through the sandbox account provided by AWS workshop studio, go to the workshop studio dashboard and click "Open AWS console" link on the left pane. This will open a AWS UI console in a new tab.
2. Head to :link[CloudWatch GenAI Observability for AgentCore]{href="https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#/gen-ai-observability/agent-core/agents" external=true}
3. Find your agent → **DEFAULT** endpoint → **Sessions** tab (see the two screenshots below)
4. Click the most recent session → click the trace
5. In the **Spans Timeline**, find the tool call span
6. Verify: the tool name is `PrebakedSQL___get_top_revenue_customers_tool` and the input includes `limit: 5`

In the **Agents** list, click the **DEFAULT** endpoint under your agent:

:image[The CloudWatch GenAI Observability Agents list, with the DEFAULT endpoint link highlighted under the agent]{src="/static/images/cw-genai-agents-default-endpoint.png"}

On the agent's endpoint page, open the **Sessions** tab:

:image[The agent endpoint page, with the Sessions tab highlighted next to Overview]{src="/static/images/cw-genai-sessions-tab.png"}

#### Part B — Check the Lambda implementation

Open :code[tools/prebaked_sql_toolset_lambda.py]{showCopyAction=true} and go to **line 403**:

```python
def get_top_revenue_customers(args):
    limit = args.get('limit', 20)
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM top_revenue_customers LIMIT %s", [limit])
```

Notice: the function queries the `top_revenue_customers` **View** — not a raw table with complex joins. The View is pre-defined in the database schema by your team. The LLM never saw this SQL. If you are interested in looking at the query implementation for this View, find it from `/workshop/agentic-analytics/dataset/schema/schema.sql`.

## Verification

- After uncommenting Step 4 and `make deploy`, the Gateway has a `PrebakedSQL` target with 20+ tools (check `make outputs` / the Gateway console)
- "Top customers" query returns data and the trace shows `get_top_revenue_customers_tool`
- You can see the View name in the Lambda code for each tool

## Troubleshooting

**Agent still says "no tools available"**
- Verify the PrebakedSQL target is `ACTIVE` in the Gateway console.

**Trace doesn't show tool calls**
- Ensure you're looking at the correct time range in CloudWatch.
- The agent may have answered from its own knowledge without calling a tool — rephrase the question to be more specific.

**Lambda returns empty results**
- The database may not have data for the specific query. Try "Show me all accounts" first to verify connectivity.

## Summary

You deployed the Prebaked SQL toolset — 29 analytics tools backed by database Views. You examined how the deploy script registers tool schemas to the Gateway, how the SOP guides the agent's tool selection, and traced a query end-to-end in CloudWatch. This is the safest pattern for production analytics — zero SQL hallucination risk.

Next, you'll connect the agent to existing business APIs → [Step 5: Integrate with Existing APIs](../05-integrate-existing-apis/)

## Reference Materials

- :link[AgentCore Gateway — Adding Targets]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-building-adding-targets.html"}
- :link[AgentCore Gateway — Tool Naming]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-tool-naming.html"}
- :link[Strands Agents — Tools]{href="https://strandsagents.com/latest/user-guide/concepts/tools/tools-overview/" external=true}
