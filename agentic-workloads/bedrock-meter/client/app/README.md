[English](README.md) | [한국어](README.ko.md)

# Bedrock Meter Console

React/Next.js console for the deployed Bedrock Meter core API.

## Architecture

The console deploys as a **single BFF Lambda behind a public HTTP API** (`$default` route, no
authorizer). The one Lambda serves both:

- the **static UI** (Next.js `output: 'export'`, bundled at `bff-lambda/dist/public`), and
- the **BFF** (`/api/console/*`) — the same `src/lib/server/*` code as local dev, bundled
  by `bff-lambda/build.mjs` and routed by `bff-lambda/handler.ts`.

One public origin, no CORS, no public S3 and no CloudFront. **Why HTTP API and not a Lambda
Function URL:** this account's SecurityHub control `lambda-function-public-access-prohibited`
blocks any Lambda resource policy with `Principal: *` (which a public Function URL requires) at
the edge — it returned 403 before the function ran. API Gateway invokes the Lambda via the
`apigateway.amazonaws.com` service principal (scoped by `SourceArn`), so it never uses a public
principal and sidesteps that control. HTTP API payload format 2.0 has the same event/response
shape as a Function URL, so `handler.ts` is unchanged. (Public S3 is likewise blocked by the
account's Block-Public-Access — both no-CloudFront direct-public options are off the table.)

This hosting model intentionally targets a **low-traffic internal operations console**. Next.js
generates HTML, JavaScript, and CSS during the build; Lambda does not perform SSR or generate HTML
per request. The generated `out/` tree is copied to `bff-lambda/dist/public/`, packaged with
`dist/index.cjs`, and read from the Lambda filesystem for each uncached static request. Hashed
`/_next/static/*` assets use a one-year immutable browser cache; HTML uses `no-cache`.

If the console grows beyond this scope, move the static export to either:

- **CloudFront + private S3 with OAC**, preferably routing `/api/console/*` to the existing API
  Gateway origin so the browser retains one origin; or
- **AWS Amplify Hosting** when independent frontend CI/CD, previews, and managed custom domains
  are more important. A separate origin requires explicit CORS, CSP, allowed-origin, and runtime
  API-base configuration.

In either design, keep Cognito token verification and Core API SigV4 signing in the BFF. Never
ship AWS credentials or direct Core API access to the browser.

The BFF signs Core API calls with SigV4 using its OWN execution-role temporary creds. Core
accepts the BFF's pinned role name (`BedrockMeterConsoleBffRole`) as an admin **caller** via the
`ADMIN_ROLE_NAMES` env, matching on the role name (the assumed-role session suffix varies).
IAM Users are enforcement targets by default. STS sessions are normalized to a stable IAM Role;
role-wide enforcement is opt-in. No long-lived IAM-user keys or SSM signing parameters are used.

## Auth (Cognito)

- Every data API requires a member of `console-admins` or `console-viewers` by default.
  Only `/api/console/config` and static UI assets are public.
- The browser attaches the Cognito **ID token** as a `Bearer` header on **all** calls; the BFF
  (`src/lib/server/cognito-auth.ts`) verifies it with `aws-jwt-verify`. `console-admins` can read
  and write; `console-viewers` are read-only. `CONSOLE_ALLOWED_USERS` is an empty-by-default
  emergency admin override.
- **Add/remove console users with no redeploy** via the Cognito group `console-admins`:

  ```bash
  POOL=<ConsoleUserPoolId>; GROUP=console-admins
  aws cognito-idp admin-create-user --user-pool-id $POOL --username alice \
    --user-attributes Name=email,Value=alice@amazon.com Name=email_verified,Value=true --message-action SUPPRESS
  aws cognito-idp admin-set-user-password --user-pool-id $POOL --username alice --password '<temp>' --permanent
  aws cognito-idp admin-add-user-to-group --user-pool-id $POOL --username alice --group-name $GROUP
  # revoke (keep the user, drop the group): admin-remove-user-from-group; for instant cutoff add admin-user-global-sign-out
  ```

  Group membership rides in `cognito:groups`, so add/remove takes effect on the user's **next token**
  (re-login or ≤60-min expiry). Empty groups plus an empty emergency allowlist fail closed.
- Login is direct Cognito `USER_PASSWORD_AUTH` from the browser (`src/lib/auth.tsx`), no amplify/SDK.
  The Cognito **region + app client id come from runtime config**, not the build (see below). When
  not logged in, the app shell shows a "Sign in required" gate instead of firing (failing) queries.
- Note: the browser can't authenticate as a *real IAM user* directly — long-lived keys can't ship to
  the browser, and Cognito Identity Pool → STS yields an assumed-role ARN that Core rejects. Cognito
  + Cognito groups are the supported operator identity model.

Every BFF response includes CSP, HSTS, frame denial, referrer, MIME-sniffing, and permissions
headers.

## Runtime Config (environment-independent build)

The static build bakes **no** environment values (no Console URL, no Cognito client id). On first
load the browser fetches `GET /api/console/config`:

```jsonc
{ "apiBase": "/api/console",
  "auth": { "provider": "cognito", "region": "...", "userPoolId": "...", "appClientId": "..." } }
```

`region`/`userPoolId`/`appClientId` are public (not secrets). The BFF deliberately omits the Core
API endpoint. `/config` is served **before** auth, so the console still boots (and can show a
config-error state) regardless of identity. `apiBase` is fixed same-origin `/api/console` — it's
how we call `/config` in the first place, so it isn't read back from the response.

## Deploy

Run from the project root with Node.js 22.12 or later (24 recommended), AWS CLI v2, and `jq`:

```bash
./scripts/deploy-all.sh --bootstrap  # first account/region bootstrap and deployment
./scripts/deploy-all.sh              # later redeployments
```

The AWS target comes from the terminal's credentials and region environment/profile settings.
`.env.deploy.local` contains project options only. The script builds the static UI, BFF, and package
before invoking CDK. Create the first console user against the same AWS target with
`scripts/add-console-admin.sh <username> <email>`; use `add-console-viewer.sh` for read-only users.

CDK fails at synth if `index.cjs` or `public/index.html` is missing. Developers invoking CDK directly
must first run `npm run build:console-artifact`.

## Local Run

The Next route handler is gone (incompatible with static export). For local UI work, point the
static app at the deployed BFF (the Cognito client id is fetched at runtime from that BFF's
`/api/console/config`, so you don't set it at build time):

```bash
npm install
NEXT_PUBLIC_CONSOLE_API_BASE="<ConsoleUrl>/api/console" npm run dev
```

`NEXT_PUBLIC_CONSOLE_API_BASE` is a dev-only override; production uses same-origin `/api/console`.
AWS credentials are never exposed to the browser — all SigV4 signing happens in the deployed BFF Lambda.

## Audit Log

The `Audit` page is backed by the persistent **`BedrockMeterAuditTable`** (DynamoDB). The core
stack's `audit-writer` Lambda subscribes to the SNS alerts topic and records `BLOCKED`,
`WARNING`, and `RESOLVED` events; admin write actions through this BFF (`LIMIT_CHANGED`,
`LIMIT_REMOVED`, `MANUAL_UNBLOCK`) are written with a `[by <email>]` actor suffix. The page reads
via the Core API `GET /admin/audit` with cursor pagination, so it survives restarts and is shared
across instances.

## User Discovery

The core API exposes `GET /users` for discovery. `BEDROCK_METER_EXTRA_USERS` is still useful for users that should appear before they have usage.

Set `BEDROCK_METER_EXTRA_USERS` for quiet users that should still appear in the console.

See the [project README](../../README.md) for installation and the documentation index.
