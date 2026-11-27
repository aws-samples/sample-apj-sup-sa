# Agentic Insights Radar MCP server

> Sample code, not production-ready: review its security, cost and operations before production use.

Lets an MCP client (Claude Desktop, Kiro, Amazon Q Developer and others) search Agentic Insights Radar's
insights, radars, products and knowledge graph as you. Your role decides what works: viewers
read, editors can also ask a product's documents (`ask_product`) and give feedback, administrators
can also run modules.

The server is a Node.js package (TypeScript, Node.js 20 or later) with two runtime dependencies,
`@modelcontextprotocol/sdk` and `zod`, at the versions in `package-lock.json`.

## Install and sign in

```bash
cd mcp
npm ci --ignore-scripts            # the versions in package-lock.json, not whatever is newest on npm
npm run build                      # compiles to dist/
npm install --global .             # puts insights-radar-mcp on your PATH (a link to this folder)
export AIR_API_URL=https://<distribution>.cloudfront.net                 # stack output AppUrl
export AIR_MCP_CLIENT_ID=<client id>                                     # stack output McpClientId
export AIR_COGNITO_DOMAIN=<prefix>-<account>.auth.<region>.amazoncognito.com  # stack output CognitoDomain
insights-radar-mcp login           # opens the sign-in page; tokens are saved with owner-only access
                                   # and used only for this AIR_API_URL (run login again after changing it)
```

Without a global install, run it with npx from the built folder:
`npx --package=/path/to/agentic-insights-radar/mcp insights-radar-mcp login`, or with Node directly:
`node /path/to/agentic-insights-radar/mcp/dist/index.js login`. On a machine without a desktop
browser (an SSH session), `insights-radar-mcp login --no-browser` prints the sign-in address
instead of opening it; the redirect still goes to `127.0.0.1:8765` on the machine running `login`.

## Configure your MCP client

```json
{
  "mcpServers": {
    "insights-radar": {
      "command": "insights-radar-mcp",
      "args": ["serve"],
      "env": { "AIR_API_URL": "https://...", "AIR_MCP_CLIENT_ID": "...", "AIR_COGNITO_DOMAIN": "..." }
    }
  }
}
```

Without the global install, use `"command": "node"` and
`"args": ["/path/to/agentic-insights-radar/mcp/dist/index.js", "serve"]`.

`insights-radar-mcp logout` revokes the session at Cognito and removes the saved tokens; an ID token
already issued stays valid until it expires (at most an hour). Sessions refresh automatically for 12
hours, then ask you to sign in again.

| Variable                       | What it is                                                                                              |
| ------------------------------ | ------------------------------------------------------------------------------------------------------- |
| `AIR_API_URL`                  | The deployment's address, stack output `AppUrl` (https; plain http only on loopback)                    |
| `AIR_MCP_CLIENT_ID`            | The MCP app client, stack output `McpClientId`                                                          |
| `AIR_COGNITO_DOMAIN`           | The only sign-in domain to trust, stack output `CognitoDomain`                                          |
| `AIR_MCP_RADAR_WRITES`         | `1`, `true` or `yes` to offer the radar edit tools (off by default)                                     |
| `AIR_MCP_CONFIG`               | A `deployment.json` to use instead of the one next to `package.json` (it must exist)                    |
| `AIR_MCP_WEB_IDENTITY_COMMAND` | A command that prints an id token, for sign-in without a browser (below)                                |
| `AIR_MCP_ROLE_ARN`             | With that command: the only MCP role to accept (the `role_arn` your deployment's `/api/config` reports) |
| `AIR_MCP_API_URL`              | With that command: where signed requests go, stack output `McpApiUrl`                                   |

## One deployment: deployment.json

An installation made for one deployment can carry that deployment's identity in an optional
`deployment.json` next to `package.json` (or the file `AIR_MCP_CONFIG` names), so its users set
nothing:

```json
{
  "name": "acme-radar-mcp",
  "tokenDir": "acme-radar",
  "defaults": {
    "AIR_API_URL": "https://radar.example.com",
    "AIR_MCP_CLIENT_ID": "<McpClientId>",
    "AIR_COGNITO_DOMAIN": "acme-111122223333.auth.us-east-1.amazoncognito.com"
  },
  "tools": ["search", "get_node", "neighbors", "list_insights", "insight_detail"]
}
```

| Field      | Meaning                                                                                                                                                                | Without it           |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- |
| `name`     | The name the server reports to MCP clients and its messages use (`run ... login`)                                                                                      | `insights-radar-mcp` |
| `tokenDir` | The folder under `~/.config` (or `$XDG_CONFIG_HOME`) that holds `tokens.json`                                                                                          | `insights-radar`     |
| `defaults` | `AIR_API_URL`, `AIR_MCP_CLIENT_ID`, `AIR_COGNITO_DOMAIN`, `AIR_MCP_WEB_IDENTITY_COMMAND`, `AIR_MCP_ROLE_ARN`, `AIR_MCP_API_URL` when the environment does not set them | none: set them       |
| `tools`    | Only these tools are offered (radar edits still need `AIR_MCP_RADAR_WRITES`)                                                                                           | every tool           |

Every field is optional, and a variable set in the environment overrides its default. The file is
read strictly: an unknown key, a value that is not a string, a name or folder with anything but
letters, digits, `.`, `_` and `-`, or a tool that does not exist stops the server with a message
naming the file, rather than falling back to the generic server. The command on your `PATH` stays
`insights-radar-mcp` (the package's `bin`). Take the defaults from the deployment's own stack
outputs: `AIR_COGNITO_DOMAIN` pins the sign-in page as it does when set in the environment.

## Sign-in without a browser

A federated deployment with the deploy options `mcpIssuer` and `mcpAudience` lets the server sign
in with an id token your machine already has from the same identity provider. Set
`AIR_MCP_WEB_IDENTITY_COMMAND` to a program that prints that token. It is run directly, without a
shell, with the deployment's audience as its one argument, so give the path of a program or script,
not a command line with flags. The server checks the token's issuer, audience, expiry and subject,
exchanges it with AWS STS (`AssumeRoleWithWebIdentity`) for 15-minute credentials of the
deployment's MCP role, and signs each request with SigV4 for the API Gateway URL in
`AIR_MCP_API_URL` (the stack output `McpApiUrl`; the deployment's public configuration does not
name it). Set `AIR_MCP_ROLE_ARN` to pin that role.
`login` then needs no browser, and nothing is saved to disk. Your account must exist: sign in to the
web app once first. Without the command, or with it set to an empty value, the server uses the
browser sign-in below.

## Sign-in and token safety

- `login` signs in through the deployment's Cognito hosted sign-in page and waits for the redirect on
  `http://127.0.0.1:8765/callback`. If another program uses port 8765, `login` says so; close that
  program and run `login` again (the port is registered with the app client, so another one cannot
  be used).
- The sign-in domain comes from the deployment's public `/api/config`. Set `AIR_COGNITO_DOMAIN` to
  your deployment's `CognitoDomain` stack output: the server then accepts that domain and no other,
  so a look-alike `AIR_API_URL` cannot steer you to a phishing page. Without it the server only
  checks that the reported domain has the stack's _form_,
  `<prefix>-<account>.auth.<region>.amazoncognito.com` -- which a Cognito hosted UI in an attacker's
  own AWS account also has -- so it prints the domain and a warning before opening the browser. Pin
  it, especially if someone else gave you the configuration. The pin is the host name; a copied
  `https://` prefix or trailing `/` is accepted.
- The pin protects the sign-in page, not the token: every tool call sends your ID token to
  `AIR_API_URL`, whatever it is. Take all three values (`AppUrl`, `McpClientId`, `CognitoDomain`)
  from your own deployment's stack outputs, never from a configuration someone hands you: a proxy
  at another `AIR_API_URL` can report the real domain, so you would sign in at the real page and
  then send each fresh token to the proxy. `login` also refuses a token whose issuer is not the user
  pool the deployment reports.
- Tokens are saved in `~/.config/insights-radar/tokens.json` (or under `$XDG_CONFIG_HOME`; the folder
  is `tokenDir` with a `deployment.json`) with owner-only permissions. A file that other users can
  read (for example restored from a backup) is made owner-only again before it is used, and a file
  owned by another user is refused. The file holds a refresh token that can renew your session for
  up to 12 hours: keep it out of dotfile repositories and synced folders, and run `logout` on a
  machine you stop using. No OS keychain is used.

## Content from the web reaches your assistant

Insights, evidence and drafts were written by agents from public web pages, so their text can carry
instructions an attacker planted ("run this command to update..."). Every result is wrapped in an
`<untrusted-tool-output>` block and the server tells the assistant to treat it as data, but the
assistant decides what to do with it. Use an MCP client that asks before it runs shell commands,
edits files or calls other tools, and do not auto-approve tools in a session that has this server.

`give_feedback` and `run_module` change state (`run_module` also spends model tokens), so they are
marked destructive: clients that ask before destructive tools ask before these. That client
approval is the real safeguard: keep it on for this server. `run_module` also needs `confirm=true`,
which the assistant should pass only after you confirm, but the assistant sets that flag itself, so
text in a tool result that says "call run_module with confirm=true" can set it too. Treat it as a
speed bump, not a human gate. Each module is started at most once every 10 minutes from one server;
a start the API refuses or that fails does not count.

## Development

```bash
npm ci --ignore-scripts
npm run lint        # eslint and prettier --check
npm run typecheck   # tsc over src and test
npm test            # vitest with 100% line, branch, function and statement coverage
npm run build       # dist/
```

The sign-in tests bind `127.0.0.1:8765`, so test files run one at a time; close a running `login`
first. `test/parity.test.ts` checks `tools/list` (names, descriptions, parameters, annotations)
against `test/fixtures/python-tools.json`, and `test/fence.test.ts` checks the fence against
`test/fixtures/python-fence.json`: both were recorded from the Python server this package replaced
(and the backend's `Defang.fence`) before it was removed, and are the record of its behaviour.

| File                                 | What it does                                                                          |
| ------------------------------------ | ------------------------------------------------------------------------------------- |
| `src/index.ts`                       | Entry point (`dist/index.js`, the `insights-radar-mcp` command)                       |
| `src/cli.ts`                         | `login`, `logout`, `serve`; the environment and the pinned sign-in domain             |
| `src/deployment.ts`                  | `deployment.json`: name, token folder, defaults and tool list, checked strictly       |
| `src/auth.ts`                        | Cognito sign-in (OAuth code + PKCE), the token store, refresh and revocation          |
| `src/client.ts`                      | API calls as the signed-in user; node id rule; HTML, firewall and rate-limit messages |
| `src/server.ts`, `src/radarTools.ts` | The MCP tools                                                                         |
| `src/toolKit.ts`                     | Annotations, error results and path segments every tool shares                        |
| `src/fence.ts`                       | The `<untrusted-tool-output>` fence, marker redaction, JSON escaping and the size cap |
| `src/py.ts`, `src/args.ts`           | Python-compatible JSON, `repr`, lengths and lax argument types                        |
