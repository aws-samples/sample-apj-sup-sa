# LibreChat config for the Internal Business Agent Tools

This directory contains the **only files** from a LibreChat deployment that you
need to copy into your local LibreChat clone in order to talk to the AgentCore
Gateway MCP endpoint produced by `../cdk`.

The LibreChat OSS itself is NOT vendored here; clone it separately:

```bash
git clone https://github.com/danny-avila/LibreChat.git librechat
cd librechat
cp ../librechat-config/librechat.yaml.example librechat.yaml  # then fill in
cp ../librechat-config/.env.example .env                       # then fill in
cp ../librechat-config/docker-compose.override.yml .
docker compose up -d
```

## Files

| File | Purpose |
|------|---------|
| `librechat.yaml.example` | LibreChat config that registers the AgentCore Gateway as an MCP server with OAuth (authorization_code + PKCE). |
| `.env.example`           | LibreChat env: Bedrock credentials, JWT/encryption secrets, Cognito OIDC for social login. |
| `docker-compose.override.yml` | Mounts `librechat.yaml` and loads `.env`. |

## Required CDK outputs

After `cdk deploy --all`, capture these and substitute into the config:

- `GatewayUrl`               (CDK output: `InternalAgent-AgentCore.GatewayUrl`)
- `<COGNITO_DOMAIN_PREFIX>`  (e.g. `internal-agent-mcp-<ACCOUNT_ID>`)
- `<COGNITO_USER_POOL_ID>`   (CDK output)
- `<COGNITO_CLIENT_ID>` / `<COGNITO_CLIENT_SECRET>` for the LibreChat client (authorization_code flow)

## Cognito App Client requirements

The LibreChat-facing Cognito App Client needs:

- `authorization_code` flow enabled
- Callback URL: `http://localhost:3080/oauth/callback` (adjust if you change `DOMAIN_SERVER`)
- Scopes: `openid`, `mcp/access`
- Identity provider(s) of your choice (Cognito user, social, etc.)

## Caveats

- LibreChat does NOT support OAuth Dynamic Client Registration (DCR). You MUST
  pre-configure `oauth.client_id` / `client_secret` in `librechat.yaml`.
- Bedrock model invocation requires inference profile IDs (`jp.*` / `apac.*`),
  not direct model IDs.
