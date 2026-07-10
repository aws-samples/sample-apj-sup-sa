---
title: "Step 3: Connect the Chat UI"
weight: 25
---

## Learning Objectives

By the end of this step, you will:
- Connect a React chat interface to your deployed AgentCore Runtime agent
- See streaming responses in a conversational UI
- Understand how the UI authenticates with Cognito and passes tokens to the agent

## Why a UI?

The CLI is great for testing, but your tenants' staff aren't going to open a terminal. They need a chat interface — something as simple as messaging a colleague.

The React UI provides:

| Feature | Why It Matters |
|---------|---------------|
| **Conversational chat** | Natural interaction for non-technical users |
| **Streaming responses** | Users see answers appearing word by word |
| **Cognito login** | User identity for access control (Steps 8-9) |
| **Query plan approval cards** | Human-in-the-loop workflows (Step 6) |

## Lab Procedures

### Step 3.1: Configure the UI

The UI needs to know the agent's runtime ARN and Cognito configuration.

```bash
cd /workshop/agentic-analytics/app/ui
```

Create a `.env` file. The script below reads the runtime ARN from your top-up stack's outputs and the Cognito values from `config.env`:

```bash
AG_CONFIG="../agentcore_strands/config.env"
STACK="agentic-analytics-agentcore"   # the top-up stack you deployed in Step 2
REGION="us-east-1"

# Runtime ARN comes from the top-up stack output (same value as `make outputs`)
AGENT_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text)

# Identity Pool id is a main-stack output
IDENTITY_POOL_ID=$(aws cloudformation describe-stacks --stack-name main-stack --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='IdentityPoolId'].OutputValue" --output text)

cat > .env << EOF
REACT_APP_AWS_REGION=$REGION
REACT_APP_AGENT_RUNTIME_ARN=$AGENT_ARN
REACT_APP_COGNITO_USER_POOL_ID=$(grep COGNITO_USER_POOL_ID $AG_CONFIG | cut -d= -f2)
REACT_APP_COGNITO_IDENTITY_POOL_ID=$IDENTITY_POOL_ID
REACT_APP_COGNITO_USER_CLIENT_ID=$(grep COGNITO_USER_LOGIN_CLIENT_ID $AG_CONFIG | cut -d= -f2)
REACT_APP_COGNITO_DOMAIN=$(grep COGNITO_DOMAIN $AG_CONFIG | cut -d= -f2)
EOF

cat .env
```

Verify the output — `REACT_APP_AGENT_RUNTIME_ARN` should show an ARN like `arn:aws:bedrock-agentcore:us-east-1:...:runtime/agentic_analytics_agent-...`. If it's empty, run `make outputs` from `../agentcore_strands` and copy the `AgentRuntimeArn` value manually.

::alert[The `REACT_APP_COGNITO_USER_CLIENT_ID` and `REACT_APP_COGNITO_DOMAIN` enable the **Login** button. Login is required — the agent needs a JWT token to authenticate with the Gateway.]{type="info"}

### Step 3.2: Start the Development Server

```bash
npm install
npm start
```

Expected output:

```
Compiled successfully!

You can now view the app in the browser.
  Local:            http://localhost:3001
```

Wait a moment — the `npm start` above should open a new browser tab with the UI. Check whether your browser is prompting you to allow a pop-up.

Alternatively, open a new browser tab and navigate to your Code Editor URL, replacing the path with `/app`:

### Step 3.3: Access the UI

The UI should be visible in a new tab now. The URL looks like the following.

```
https://<your-cloudfront-domain>/app
```

For example it will be https://xxxxxxxxxxxxxx.cloudfront.net/app

::alert[The `/app` path is pre-configured in the Code Editor's Nginx proxy to forward requests to the React dev server on port 3001. No port forwarding setup is needed.]{type="info"}

::alert[**Login callback is pre-registered for you.** Your CloudFront `/app` URL was automatically added to the Cognito app client's allowed callback/logout list when the environment was provisioned (along with `http://localhost:3001/app` for local dev), so Login just works. The redirect the app sends is `window.location.origin + /app`. *If* you ever reach the UI by some other host/path and see `redirect_mismatch`, add that exact `…/app` URL under the user pool client's **Allowed callback URLs** (Cognito console → User pool → App client → Hosted UI) — but in the standard workshop flow you won't need to.]{type="info"}

You should see the Timely-Unicorn Analytics dashboard with:
- A chat panel on the left with suggested queries
- A dashboard on the right showing business stats (2 businesses, 100 unicorns, ~500 customers)
- A **Login** button in the top right

### Step 3.4: Log In

Click the **Login** button. You'll be redirected to the Cognito Hosted UI. Log in as one of the Mythical Unicorns staff:

| Field | Value |
|-------|-------|
| Username | `stella.moonbeam@example-mythicalunicorns.com` |
| Password | `Unicorn123!` |

After login, you'll be redirected back to the chat UI. The top-right should now show the logged-in user.

::alert[Login is required — the agent needs a JWT token to authenticate with the Gateway. Without logging in, chat messages will fail.]{type="warning"}

### Step 3.5: Test Basic Connectivity

The agent doesn't have analytics tools yet — those come in Step 4. For now, verify the UI connects to the agent:

- "Hello, what can you help me with?"
- "What kind of questions can I ask?"

The agent should respond conversationally, describing its capabilities. It won't be able to answer data questions yet — that's expected. However, it can already answer question like "What time is it?" since it has the datetime tool. It already has memory as well after we set it up in step 2.

::alert[**No tools yet.** The agent is deployed to Runtime with a Gateway, but no toolsets are registered. In the next step, you'll deploy the Prebaked SQL toolset and come back to the UI to test real analytics queries.]{type="info"}

## Verification

- `npm start` compiles without errors
- The chat UI loads at `https://<cloudfront-domain>/app`
- You can type a message and receive a streaming response
- The agent responds conversationally (no data queries yet — tools come in Step 4)

## Troubleshooting

**`npm install` fails with permission errors**
- Run `npm install --legacy-peer-deps` if there are dependency conflicts.

**UI loads but shows "Connection error"**
- Verify `REACT_APP_AGENT_RUNTIME_ARN` in `.env` is correct.
- Ensure the agent is still deployed: run `make outputs` from `app/agentcore_strands` and confirm `AgentRuntimeArn` is present.

**`/app` shows 502 Bad Gateway**
- The React dev server may not be running. Run `npm start` in the `app/ui` directory.

::alert[Always use `npm start` to start the UI server — never call `react-scripts start` directly. The `package.json` has `PORT=3001` and `PUBLIC_URL=/app` baked into the start script. Calling `react-scripts` directly skips these, causing assets to load from wrong paths (blank page).]{type="warning"}

**Responses are empty or the agent doesn't respond**
- Check the browser console (F12 → Console) for errors.
- Confirm the Runtime reached `READY` after your last `make deploy` / `make build` (check the stack status with `make status`).

## Summary

You connected a React chat UI to your deployed agent, giving business users a natural conversational interface. The UI handles streaming, authentication, and will support advanced features like SQL approval cards in later steps.

Next, you'll deploy the analytics toolset so the agent can answer real data questions → [Step 4: Deploy Prebaked SQL Toolset](../../02-toolsets/04-deploy-prebaked-sql/)

## Optional: Deploy UI to Cloud (AWS Amplify)

::alert[**This step is optional.** The local dev server via `npm start` is sufficient for the rest of the workshop. Only follow this if you want a production-grade UI hosting, that has URL with HTTPS and CDN.]{type="warning"}

Until now, the React UI runs locally on the EC2 Code Editor via `npm start` and port forwarding. For production, your tenants need a real URL. :link[AWS Amplify Hosting]{href="https://docs.aws.amazon.com/amplify/latest/userguide/welcome.html"} provides managed hosting with HTTPS, CDN, and CI/CD — no servers to manage.

### Step 3.6: Deploy to Amplify

Open a new terminal window by clicking `+` button on the Code Editor's terminal pane.

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
python3 ui/deploy_amplify_hosting.py
```

This script builds the React app, creates an Amplify app and branch, deploys the built assets, and configures environment variables. Expected output:

```
Building React application in /workshop/agentic-analytics/app/ui
Build complete: /workshop/agentic-analytics/app/ui/build
Creating deployment ZIP from /workshop/agentic-analytics/app/ui/build
Created ZIP: /tmp/tmpxxxxxxxx.zip (1.17 MB)
Creating Amplify app: agentic-analytics-ui
Creating branch: main
Starting deployment from /tmp/tmpxxxxxxxx.zip
Uploading to Amplify (job: 1)
Waiting for deployment 1
Status: PENDING
Status: SUCCEED
  No gateway_config.json — skipping Cognito callback update

╔══════════════════════════════════════════════════════════════════╗
║                    Deployment Complete!                          ║
╠══════════════════════════════════════════════════════════════════╣
║  App ID:  xxxxxxxxxxxxxx                                       ║
║  URL:     https://main.xxxxxxxxxxxxxx.amplifyapp.com           ║
╚══════════════════════════════════════════════════════════════════╝
```

Open the Amplify URL in your browser — you should see the same chat interface, now hosted on a real URL with HTTPS. All features (streaming, login, policy, RLS, guardrails) work the same way through the cloud-hosted UI.

## Reference Materials

- [AgentCore Runtime — Invoking Agents](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-invoke.html)
- [AgentCore SDK Python](https://github.com/aws/bedrock-agentcore-sdk-python)
- :link[AWS Amplify Hosting]{href="https://docs.aws.amazon.com/amplify/latest/userguide/welcome.html"}
- :link[Amplify — Manual Deployments]{href="https://docs.aws.amazon.com/amplify/latest/userguide/manual-deploys.html"}
