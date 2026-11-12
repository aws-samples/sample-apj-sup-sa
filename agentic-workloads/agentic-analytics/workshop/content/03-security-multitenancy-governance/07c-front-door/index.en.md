---
title: "Step 7c: Put a Front Door in Front of the Agent"
weight: 52
---

## Learning Objectives

By the end of this step, you will:
- Put an AgentCore Gateway with AWS WAF and a per-user rate limit in front of the agent runtime
- Make that gateway the only way to reach the runtime
- Know why the user's JWT still reaches your tools, and when you would need on-behalf-of token exchange instead

## The problem

Until now the UI has called the agent runtime directly. The runtime checks the Cognito JWT, and that is all. Anyone with a valid token can send as many requests as they like, and nothing inspects the request before the model sees it.

Production APIs usually sit behind a front door that blocks known-bad traffic and limits how fast each caller can go. In this step you add one without changing the agent's code.

## How the front door works

```
UI / voice bot --(Cognito JWT)--> Front-door Gateway --(SigV4, gateway role)--> Agent Runtime --> Tools Gateway
                                   |  JWT authorizer
                                   |  AWS WAF (IP rate, IP reputation, bad inputs)
                                   |  per-user rate limit (JWT sub)
```

- The front door is an AgentCore Gateway with one target: your agent runtime.
- The gateway checks the user's JWT, applies the WAF rules and the per-user limit, then calls the runtime with its own IAM role.
- The runtime switches from JWT auth to IAM auth, and a resource policy lets only the front door's role in. A direct call to the runtime now fails, so nobody can go around the WAF.
- The UI sends the user's JWT twice: as `Authorization` for the gateway, and in the `X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Token` header, which the gateway forwards to the runtime. The agent passes that token on to the tools gateway, so Cedar policies and Row-Level Security from Step 7 keep working per user.

## Lab Procedures

### Step 7c.1: Turn the front door on

There is no fence to uncomment this time. The front door is one stack parameter:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make deploy FRONT_DOOR=true
```

This takes a few minutes. When it finishes, get the front-door URL:

```bash
make outputs | grep FrontDoorUrl
```

::alert[`FRONT_DOOR` is remembered by the stack. A later plain `make deploy` keeps the front door on; run `make deploy FRONT_DOOR=false` to remove it.]{type="info"}

### Step 7c.2: See what changed

Open :code[agentcore-topup-stack.yaml]{showCopyAction=true} and search for `FrontDoorGateway`. Look at three resources:

1. **`FrontDoorGateway`** has a Cognito JWT authorizer, like your tools gateway, but no `ProtocolType`. That makes it an HTTP gateway, which is the kind that can have a runtime as its target.
2. **`FrontDoorAgentTarget`** points at your runtime and uses `GATEWAY_IAM_ROLE`, so the gateway signs each call with its own role. Its header allowlist passes on the user-token and session-id headers.
3. **`AgentRuntimeResourcePolicy`** denies every principal except the front door's role.

The WAF web ACL is attached with an `AWS::WAFv2::WebACLAssociation`. Per-IP rate, IP reputation and known-bad-input rules block. The Common and SQL-injection rule groups only count by default (`WafBodyRulesAction=COUNT`), because prompts are free text and words like "select" or "drop" are normal in analytics questions.

### Step 7c.3: Point the UI at the front door

Add the URL to the UI's `.env` and restart the dev server, because React reads `.env` only at start-up:

```bash
cd /workshop/agentic-analytics/app/ui
FRONTDOOR_URL=$(aws cloudformation describe-stacks --stack-name agentic-analytics-agentcore --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='FrontDoorUrl'].OutputValue" --output text)
echo "REACT_APP_FRONTDOOR_URL=$FRONTDOOR_URL" >> .env
grep FRONTDOOR .env
```

Stop `npm start` with **Ctrl+C**, then run it again:

```bash
npm start
```

### Step 7c.4: Test it

1. Reload the UI, sign in as `lyra.starwhisper@example-mythicalunicorns.com` and ask "How many unicorns do we have?". The answer comes back as before, now through the front door.
2. Ask a question that touches tenant data, such as "Who are our top 3 customers by revenue?". You still see only Mythical Unicorns customers, which shows the user token reached the tools.
3. Try to go around the front door. Comment out the `REACT_APP_FRONTDOOR_URL` line in `.env`, restart `npm start`, and ask again. The UI calls the runtime directly and gets an error: the runtime no longer accepts JWTs and its resource policy refuses everyone except the front door. Put the line back and restart before you go on.

## JWT propagation or on-behalf-of?

This workshop forwards the user's own Cognito JWT all the way to the tools. That works because every hop trusts the same Cognito user pool and the tools are yours: the tools gateway validates the token again, and the interceptor hands it to the Lambda functions for Row-Level Security.

You need on-behalf-of (OBO) token exchange when a downstream service trusts a different identity provider, or expects a token with its own audience and narrower scopes, for example a SaaS API that only accepts its own OAuth tokens. Then the agent trades the user's token for a new one issued for that service, through AgentCore Identity and an OAuth authorization server that supports token exchange (RFC 8693). The resource side needs nothing new beyond accepting the tokens its own authorization server issues.

## Verification

- `make deploy FRONT_DOOR=true` finishes, and `make outputs` shows a `FrontDoorUrl`.
- The UI works with `REACT_APP_FRONTDOOR_URL` set, and tenant isolation still holds.
- With the URL removed, the direct call to the runtime fails.

## Troubleshooting

**The UI shows 401 after the switch**
- Sign out and in again so the UI has a fresh token, and check that `REACT_APP_FRONTDOOR_URL` has no trailing `/agent/invocations`; the UI adds that path itself.

**Requests start failing after many quick questions**
- You hit the per-user rate limit, `FrontDoorRequestsPerMinute` (default 30 per minute). Wait a minute and try again.

## Summary

The agent now has one entrance, and it checks the token, filters traffic and limits each user before any model call. Next you will add content guardrails → [Step 8: Guardrails](../08-guardrails/)

## Reference Materials

- :link[AgentCore Gateway: supported targets]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-supported-targets.html" external=true}
- :link[AWS WAF: rate-based rules]{href="https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based.html" external=true}
- :link[OAuth 2.0 Token Exchange (RFC 8693)]{href="https://www.rfc-editor.org/rfc/rfc8693" external=true}
