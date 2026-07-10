---
title: "Step 5: Integrate with Existing APIs"
weight: 22
---

## Learning Objectives

By the end of this step, you will:
- Understand how to connect your agent to existing business APIs
- Deploy an API integration toolset as a new Gateway target
- See the agent autonomously chain multiple tools to fulfill a complex request

## The Pattern: Connecting Agents to Existing APIs

Most SaaS platforms already have APIs — for booking, billing, notifications, inventory. Rather than rebuilding these, you connect the agent to them. The LLM's job is to:
1. **Pick which API to call** (each API maps to a tool)
2. **Extract inputs** from the conversation (customer name, date, unicorn)
3. **Ask for missing information** if the user didn't provide everything
4. **Call the API** and return the result

::alert[**Two simplifications in this workshop:** (1) Instead of connecting to an existing API, we deploy the API Lambda in this step as opposed of having it predeployed — but the spirit is to connect to existing deployed API. (2) Instead of calling API Gateway or ALB, the Gateway calls the Lambda directly for simplicity. AgentCore Gateway supports integration with multiple targets, including API Gateway, OpenAPI, Smithy models, MCP servers, and Lambda functions. See the full list :link[here]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-supported-targets.html"}]{type="info"}

## How AgentCore Gateway Connects to APIs

For this workshop, we use a **Lambda target** for the booking tool because it supports JWT propagation through the Gateway Interceptor in simple way — essential for Row-Level Security in Step 7.

The booking tool is a **separate Lambda and target** (`APIInteg`) from the analytics tools (`PrebakedSQL`). This separation is intentional — it enables fine-grained access control in the next step, where Cedar policies reference targets by name.

## Lab Procedures

### Step 5.1: Uncomment the API Integration toolset and deploy

Open :code[/workshop/agentic-analytics/app/agentcore_strands/agentcore-topup-stack.yaml]{showCopyAction=true} and find the **Step 5** fence:

```
# ===== UNCOMMENT FROM HERE (Step 5: API integration toolset ...) =====
...
# ===== UNCOMMENT TO HERE (Step 5) =====
```

Uncomment everything between the markers (this brings up `ApiIntegLambda`, its role + permission, and the `ApiIntegTarget`).

::alert[**Tip — uncomment the whole block at once.** Don't delete each `#` by hand. The Code Editor is VS Code: click the first line *inside* the fence, then **Shift+click** the last line inside it to select the whole block, and press **Cmd + /** (macOS) or **Ctrl + /** (Windows/Linux) to toggle the comments off for every selected line in one go. Select only the lines **between** the two `UNCOMMENT` markers — not the marker lines themselves.]{type="info"}

Then deploy:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make deploy
```

This adds:
- **Lambda function** with booking validation, cost calculation, and reference generation
- **Gateway target** (`APIInteg`) registered on your existing Gateway

::alert[**Pattern for your SaaS:** The `APIInteg` target is your integration point for existing business APIs. Here we start with booking creation, but the same Lambda can host any write operation — cancellations, billing, notifications, inventory updates. Each function you add becomes a new agent-callable tool. This is how you connect your agent to your existing microservices.]{type="info"}

### Step 5.2: Examine what you uncommented — Tool Schema

Still in :code[agentcore-topup-stack.yaml]{showCopyAction=true}, look at the `ApiIntegTarget` you just uncommented — its `ToolSchema.InlinePayload`:

```yaml
- Name: create_booking_tool
  Description: Create a new unicorn booking. Validates unicorn availability and calculates cost ...
  InputSchema:
    Type: object
    Properties:
      customer_id: { Type: string, Description: The customer ID making the booking }
      unicorn_id: { Type: string, Description: The unicorn ID to book }
      start_datetime: { Type: string, Description: 'Booking start time in ISO format ...' }
      end_datetime: { Type: string, Description: 'Booking end time in ISO format ...' }
      # ...
    Required: [customer_id, unicorn_id, start_datetime, end_datetime]
```

Compare this to the PrebakedSQL tools from Step 4 — notice the `Required` field. The LLM knows it must extract these 4 values before calling the tool. If the user says "book a unicorn for tomorrow", the agent will ask for the missing customer and unicorn instead of guessing.

The target is registered under a separate name, `APIInteg`:

```yaml
ApiIntegTarget:
  Type: AWS::BedrockAgentCore::GatewayTarget
  Properties:
    GatewayIdentifier: !GetAtt Gateway.GatewayIdentifier
    Name: APIInteg
    # ...
```

This means the agent sees the tool as `APIInteg___create_booking_tool`. The target name prefix is what Cedar policies reference in Step 7 to control who can call write tools.

### Step 5.3: Understand the SOP — Booking Creation Workflow

Open :code[unicorn_rental_analytics.sop.md]{showCopyAction=true} and review:

**Lines 61-80 — Booking Creation Workflow:** This is the goal-oriented workflow the agent follows when a user asks to create a booking. It defines the 4 required parameters (customer_id, unicorn_id, start_datetime, end_datetime) and how to resolve each one. Key constraints:
- *"You MUST resolve relative dates to absolute dates before creating the booking"*
- *"You MUST NOT guess customer or unicorn IDs — resolve them via search tools, from the conversation history, or ask the user"*
- *"You MUST NOT ask for confirmation before creating the booking — execute directly"*

**Lines 145-155 — Example 2:** A complete booking example showing the tool chain: `current_datetime` → `search_customers_tool` → `search_unicorns_tool` → `create_booking_tool`.

::alert[**SOP + Policy = Defense in Depth.** The Policy Engine hides the tool at the Gateway level (the agent can't call it). The SOP's graceful failure handling means the agent will inform the user if a tool isn't available. Together, they provide both infrastructure-level and application-level access control.]{type="info"}

### Step 5.4: Test It

Booking creation requires a staff or admin role. In the chat UI, log in as:

| Field | Value |
|-------|-------|
| Username | `stella.moonbeam@example-mythicalunicorns.com` |
| Password | `Unicorn123!` |

::alert[**Use the right user:** In case you intend to run the below queries after deploying the AgentCore Policy and RLS in step 7, you MUST use users with "staff" or "rental_admin" or "saas_admin" type to be able to use the create booking tool. The user above (Stella Moonbeam) is a staff member. If you run this step before deploying the components in step 7, the user does not matter.]{type="info"}

::alert[**Start fresh:** It is best to clear the chatbot conversation from the previous step by clicking the small bin icon next to the chat input field or by refreshing the application demo browser tab.]{type="info"}

Then try:

> "Create a booking for unicorn Vega Sapphire tomorrow for my top customer"

Note that we purposefully omit the timing information. When the agent asks, you can say 

> "10 am for 3 hours."

Watch the agent chain multiple tools autonomously:

1. **`current_datetime`** — resolves "tomorrow" to an actual date (local tool, instant)
2. **`get_top_revenue_customers_tool`** — finds the top customer (Gateway → Lambda → Aurora)
3. **`search_unicorns_tool`** — finds Vega Sapphire's unicorn ID
4. **`create_booking_tool`** — validates availability, calculates cost, creates booking (Gateway → separate Lambda → Aurora)

The agent orchestrated 4 tool calls across 2 different Lambda functions, resolved relative dates, and created a booking — all from a single natural language request.

## The Concern

Now think about this: **any user** with access to the agent can create bookings. An analyst who should only have read access could say "create a booking" and it would work. That's a security gap.

We'll fix this in the next step with AgentCore Policy.

## Verification

- After uncommenting Step 5 and `make deploy`, the stack updates without errors
- The `APIInteg` target appears in the Gateway console alongside `PrebakedSQL`
- The agent can create a booking via natural language
- The booking response includes a booking reference, cost, and confirmation

## Troubleshooting

**Agent says "I don't have a tool to create bookings"**
- Verify the `APIInteg` target is `ACTIVE` in the Gateway console.

**Booking fails with "unicorn not available"**
- The sample data has specific unicorns. Try: "Book any available unicorn for customer Mfaranwe Quoralis at Mythical Unicorns tomorrow 10am to 2pm"

## Summary

You deployed a write operation as a separate Gateway target, and the agent autonomously chained 4 tools to create a booking from a single natural language request. The separation of read (`PrebakedSQL`) and write (`APIInteg`) targets is a deliberate design choice that enables fine-grained access control.

But right now, any user can create bookings. We'll fix this with AgentCore Policy in Step 7. First, let's add the final toolset → [Step 6: Custom SQL for Ad-Hoc Queries](../06-custom-sql/)

## Reference Materials

- [AgentCore Gateway — Creating Targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets.html)
- [Strands Agents — Tool Chaining](https://strandsagents.com/latest/user-guide/concepts/tools/tools-overview/)
