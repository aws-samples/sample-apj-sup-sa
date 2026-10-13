# Local CLI Application to Invoke AgentCore Runtime

A simple command-line client to act as a sample application that invokes the deployed **AgentCore Runtime** as a
**Cognito user** (`user1` by default), exercising the 3LO passthrough end to end. 

Purpose:

1. Reads the deployed resource ids from the `SlackGatewayStack` CloudFormation
   outputs (`RuntimeArn`, `UserPoolId`, `UserPoolClientId`) — nothing is hardcoded.
2. Signs the user in with username/password (`ADMIN_USER_PASSWORD_AUTH`) and takes
   their Cognito **access token**.
3. Launches `callback_server.py` in the background (bound to that token) so first-time
   Slack OAuth consent redirects can be completed at `http://localhost:8080/callback`.
4. POSTs the prompt to the runtime data-plane `/invocations` endpoint with the token
   as the bearer and streams the SSE response.

> `boto3.invoke_agent_runtime` is intentionally **not** used: it signs the request
> with SigV4, but the runtime's `CUSTOM_JWT` authorizer expects a Cognito JWT bearer.

## Prerequisites

- The stack is deployed (`cd ../slack-gateway-cdk && npx cdk deploy`).
- **You are logged in to AWS in your CLI** for the account the stack was deployed to. The
  CLI reads its credentials from your environment, so sign in before running any commands
  below — for example run `aws sso login` (if you use IAM Identity Center / SSO), or export
  temporary credentials by copying/pasting the access key, secret access key, and session
  token into your shell:

  ```bash
  export AWS_ACCESS_KEY_ID="<access-key-id>"
  export AWS_SECRET_ACCESS_KEY="<secret-access-key>"
  export AWS_SESSION_TOKEN="<session-token>"   # if using temporary credentials
  ```

  Verify with `aws sts get-caller-identity`. The CLI calls CloudFormation, Cognito
  `describe-user-pool-client`, and `admin-initiate-auth`, so these credentials must be
  active and for the same account/region the stack was deployed to.
- Python 3.9+.

## Setup

```bash
cd cli
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Username/password are read from .env (already set to user1). To customize:
cp .env.example .env   # then edit COGNITO_USERNAME / TEST_USER_PASSWORD
```

## Usage

It runs as an **interactive shell**: it signs in once, then holds a back-and-forth
conversation with the agent. A single runtime session id is reused for the whole
session, so the agent keeps context across turns. Type `exit`/`quit` (or Ctrl-D) to stop.

```bash
# Username + password come from .env (user1 by default). Starts interactive chat.
python invoke_runtime.py

# Send an opening prompt, then drop into the chat loop
python invoke_runtime.py "list the slack users"

# Chat as a different seeded user
python invoke_runtime.py --user user2

# Override stack / region if you deployed elsewhere
python invoke_runtime.py --stack SlackGatewayStack --region us-east-1
```

Example session (first-run 3LO consent, then a Slack post):

```
Signing in as user1...
Signed in as user1. Session: 3f9a1c2b8d0e...

Callback server running on http://localhost:8080/callback (pid 41234).
If a response contains an authorization URL, open it in your browser to
complete OAuth session binding, then retry the prompt.

Interactive mode. Type 'exit' or 'quit' (or Ctrl-D) to stop.

you> Post a message "Deployment is done, thanks all!" to #general chat in Slack
agent>   [tool] chat_postMessage
agent> You haven't authorized Slack yet. Open this URL in your browser to grant
       access, then ask me to try again:
       https://slack.com/oauth/v2/authorize?client_id=...&user_scope=chat%3Awrite%2C...

# (open the URL, approve in Slack; the browser redirects to
#  http://localhost:8080/callback and the callback server logs)
Session binding complete.

you> try again
agent>   [tool] chat_postMessage
agent> Done — posted "Deployment is done, thanks all!" to #general channel as you.
you> exit
Shutting down callback server...
```

## OAuth session binding (3LO)

The first time a Slack tool call runs, the gateway needs the signed-in user to
consent to Slack. The runtime surfaces an **authorization URL** in the response; open
it in your browser and approve. Slack redirects back to
`http://localhost:8080/callback` with a `session_id`, and the bundled callback server
calls AgentCore Identity `CompleteResourceTokenAuth` to bind that consent to your
session. After that, retry the prompt — subsequent tool calls act as you (3LO).

The gateway's OAuth provider is configured with
`DefaultReturnUrl = http://localhost:8080/callback`, so the server must own port 8080.
`invoke_runtime.py` starts it automatically (freeing the port first if needed) and
shuts it down on exit. You can also run it standalone:

```bash
python callback_server.py --user-token "<cognito_access_token>" --region us-east-1
```

## Config resolution

Each value resolves as **CLI flag > environment variable > default/auto-discovery**:

Environment variables are loaded from `.env` (next to the script) if present.

| Value | Flag | Env (`.env`) | Default |
| --- | --- | --- | --- |
| Opening prompt | positional arg | — | optional (else straight to chat) |
| User | `--user` | `COGNITO_USERNAME` | `user1` |
| Password | `--password` | `TEST_USER_PASSWORD` | interactive prompt |
| Stack | `--stack` | `STACK_NAME` | `SlackGatewayStack` |
| Region | `--region` | `AWS_REGION` | `us-east-1` |
