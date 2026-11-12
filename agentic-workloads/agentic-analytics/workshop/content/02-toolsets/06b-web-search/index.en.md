---
title: "Step 6b: Public Web Context with Web Search"
weight: 24
---

## Learning Objectives

By the end of this step, you will:
- Add AgentCore Web Search to your Gateway as a connector target
- See the agent combine your own data with public facts in one answer
- Know which rules keep tenant data out of search queries

## Why the agent needs the web

Your tools answer questions about your own business: bookings, revenue, unicorns. Some questions also need facts that live outside the database. "Why did bookings jump in the second week of April?" is half a data question and half a calendar question, because the answer may be a public holiday.

AgentCore Web Search is an AWS-run search tool that you attach to a Gateway like any other target. There is no API key to manage. The Gateway calls it with its own IAM role, and the agent sees one more MCP tool called `WebSearch`.

## Lab Procedures

### Step 6b.1: Uncomment the Web Search target and deploy

Open :code[/workshop/agentic-analytics/app/agentcore_strands/agentcore-topup-stack.yaml]{showCopyAction=true} and find the **Step 6b** fence:

```
# ===== UNCOMMENT FROM HERE (Step 6b: Web Search connector) ======
...
# ===== UNCOMMENT TO HERE (Step 6b) ======
```

Uncomment the lines between the markers, the same way as in Step 5, then deploy:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make deploy
```

### Step 6b.2: Read what you uncommented

The whole target is about 20 lines:

```yaml
WebSearchTarget:
  Type: AWS::BedrockAgentCore::GatewayTarget
  Properties:
    GatewayIdentifier: !GetAtt Gateway.GatewayIdentifier
    Name: WebSearch
    TargetConfiguration:
      Mcp:
        Connector:
          Source:
            ConnectorId: web-search
          Configurations:
            - Name: WebSearch
              ParameterValues:
                domainFilter:
                  exclude: [facebook.com, instagram.com, tiktok.com, reddit.com, x.com, twitter.com]
    CredentialProviderConfigurations:
      - CredentialProviderType: GATEWAY_IAM_ROLE
```

Three things to notice:

1. **`Connector` instead of `Lambda`.** You wrote no code for this tool. AWS runs the search, and the connector exposes it as an MCP tool.
2. **`GATEWAY_IAM_ROLE`.** The Gateway signs the call with its own role, which needs `bedrock-agentcore:InvokeWebSearch`. The user's JWT is not sent to the search service. The Gateway interceptor forwards the user token only to your own Lambda targets, where Row-Level Security needs it.
3. **`domainFilter.exclude`.** The Gateway enforces this list, so the agent cannot search social media sites even if a prompt asks it to.

### Step 6b.3: See the SOP rules for web search

Open :code[unicorn_rental_analytics.sop.md]{showCopyAction=true} and find section **4c. Public web context (WebSearch)**. The rules that matter most:

- The agent answers the data part of a question with your tools first, then adds public context.
- The agent must never put tenant or customer data into a search query.
- Every web fact in the answer comes with its source.

### Step 6b.4: Test it

Start a new chat (bin icon next to the chat input) and sign in as the rental admin:

| Field | Value |
|-------|-------|
| Username | `lyra.starwhisper@example-mythicalunicorns.com` |
| Password | the password from `make users` (Step 3.4) |

Ask:

> "Which week in April had the most bookings, and was there a public holiday in the US that week?"

The agent should call a booking tool for the weekly numbers, then call `WebSearch` for the holiday, and cite the source it used. Open the tool trace in the UI and check the search query: it should mention the dates and the holiday, and nothing about your customers.

Then ask something the database already answers:

> "How many unicorns do we have?"

This time there should be no web search.

## Verification

- `make deploy` finishes without errors.
- The Gateway console shows a `WebSearch` target next to `PrebakedSQL`, `APIInteg` and `CustomSQL`.
- The holiday question returns booking numbers plus a cited public fact.
- The search query in the trace contains no customer names or IDs.

## Troubleshooting

**The agent says it has no web search tool**
- Check that the `WebSearch` target is `READY` in the Gateway console, then start a new chat so the agent reloads its tool list.

**The agent answers from memory instead of searching**
- Ask for a source ("...and cite where you found it"). The SOP tells the agent to search for facts it has to cite.

## Summary

You added a public web tool without writing code, and the Gateway decides which sites it may reach. Right now every signed-in user can search the web. In Step 7 you will write a Cedar policy that removes web search for the staff role.

Next → [Step 7: Agent Access Policy](../../03-security-multitenancy-governance/07-agent-access-policy/)

## Reference Materials

- [AgentCore Gateway: supported targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-supported-targets.html)
