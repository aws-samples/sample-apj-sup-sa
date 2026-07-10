---
title: "Troubleshooting"
weight: 90
---

Known issues that workshop participants have hit. Skim this if something fails unexpectedly.

### 0. No "Agentic Commerce" section in the Stripe Dashboard

**Symptom.** You can't find the "Agentic Commerce" nav entry where Chapter 2 says you'll copy the `profile_test_...` from. You may also not see "Machine Payments" in Settings → Payment methods.

**Cause.** Agentic Commerce is a Stripe preview product, currently **US-only**, and needs to be requested per account.

**Fix.**

- Make sure your Stripe account is in **Sandbox** mode (top-right toggle).
- Settings → Payment methods — look for "Agentic Commerce" / "Stablecoins and Crypto" / "Machine payments". Request access if you see a **Request access** button.
- If none of those entries exist at all, your account may not have access to the preview. Two options:
  - (Best) Skip the real-Stripe path and leave both seller secrets as `PLACEHOLDER`. The Lambda runs in mock mode and the workshop still completes end-to-end — you just won't see PaymentIntents in any dashboard.
  - Try a separate Stripe test account with a US-registered legal entity. Agentic Commerce requires a US business.

### 1. `AccessDenied` on `cloudfront:CreateInvalidation` at the end of `participant-deploy.sh`

**Symptom.** The last line of the step-6 deploy script logs:

```
aws: [ERROR] An error occurred (AccessDenied) when calling the CreateInvalidation
operation: User: arn:aws:sts::...:assumed-role/...CodeEditorRole-... is not authorized
to perform: cloudfront:CreateInvalidation on resource: arn:aws:cloudfront:...
```

**Cause.** The Code Editor EC2 instance role is missing `cloudfront:CreateInvalidation`.

**Fix.** Safe to ignore — the script already uploaded `config.json` to S3 successfully by the time CloudFront invalidation runs. The SPA's `/config.json` path uses a zero-TTL cache policy, so a hard-refresh (Cmd+Shift+R / Ctrl+Shift+F5) in the SPA tab is equivalent. If you want the permission added, ask the workshop admin to patch `infrastructure/stacks/code-editor-stack.yaml`.

### 2. `exec /bin/sh: exec format error` during the Samurai container build

**Symptom.** `./workshop/code/participant/participant-deploy.sh` fails mid-Docker-build with:

```
ERROR [4/8] RUN npm install --omit=dev
0.342 exec /bin/sh: exec format error
```

**Cause.** The default `docker` builder can't cross-build `linux/arm64` images on an x86 host. AgentCore Runtime requires arm64.

**Fix.** Initialise a `docker buildx` builder once, then re-run the deploy script:

```bash
docker buildx create --name wsbuilder --driver docker-container --use
docker buildx inspect --bootstrap
docker buildx ls                      # confirm wsbuilder has a * next to it
./workshop/code/participant/participant-deploy.sh
```

### 3. SPA shows *"Auth UserPool not configured"*

**Symptom.** After login attempt, the Samurai SPA shows a red banner: `Auth UserPool not configured.`

**Cause.** The browser is serving a stale `config.json` from before the SPA deployed, or the CloudFront invalidation (see issue #1) didn't run.

**Fix.** Hard-refresh the SPA tab:

- macOS: `Cmd+Shift+R`
- Windows/Linux: `Ctrl+Shift+F5`

If that doesn't help, open DevTools → Network, select "Disable cache", and reload.

### 4. SPA chat shows *"Received error (500) from runtime. Please check your CloudWatch logs."*

**Symptom.** The SPA accepts the message but the chat returns a generic 500.

**Causes & fixes:**

- **Most common:** AgentCore Runtime isn't `READY` yet. Re-run the verify command from step 6:
  ```bash
  aws bedrock-agentcore-control list-agent-runtimes \
    --region "$AWS_REGION" \
    --query "agentRuntimes[?contains(agentRuntimeName, 'samurai')].[agentRuntimeName,status]" \
    --output table
  ```
  If status is `CREATING`, wait. If `FAILED`, check CloudWatch logs at `/aws/bedrock-agentcore/runtimes/samurai_*`.
- **Session token expired.** Sign out and back in via the SPA — the Cognito session is short-lived.
- **Runtime ARN missing in `config.json`.** The deploy script failed before writing the ARN. Check S3: `aws s3 cp s3://$SPA_BUCKET/config.json -` and confirm `samuraiAgentRuntimeArn` is populated.

### 5. `npm run build` fails with type errors in `memory.ts` after TODO 5

**Symptom.** Running `npm run build` inside `app/samurai-agentcore` reports something like:

```
src/memory.ts: error TS2304: Cannot find name 'ListEventsCommand'.
src/memory.ts: error TS2304: Cannot find name 'CreateEventCommand'.
```

**Cause.** TODO 5.1 / 5.2 fills in the SDK call body but the import at the top of `memory.ts` got dropped.

**Fix.** Open `app/samurai-agentcore/src/memory.ts` and confirm the top imports look like:

```ts
import {
  BedrockAgentCoreClient,
  CreateEventCommand,
  ListEventsCommand,
} from '@aws-sdk/client-bedrock-agentcore'
import type { MessageData } from '@strands-agents/sdk'
```

Re-run `npm run build`.

### 6. `www-authenticate` does not appear in step 3 verify

**Symptom.** The curl grep for `www-authenticate|x-amzn-remapped-www-authenticate` returns nothing on a 402 response.

**Cause.** Likely one of:

- The grep pattern didn't include `x-amzn-remapped-www-authenticate` — API Gateway remaps the original header, so both names exist on the response. The step-3 grep pattern has been updated; make sure you're using the current one.
- The Lambda built and deployed but `Mppx.create({...})` threw before the 402 could be built. Check CloudWatch logs for the Lambda — missing or PLACEHOLDER `networkId`/`secretKey` can cause this in real (non-mock) mode.

### 7. Retry returns `402` instead of `200`

**Symptom.** Samurai's `generate_listing` tool reports `Payment failed: ...` or the MPP debug panel loops 402 → 402 without reaching 200.

**Causes & fixes:**

- **Seller secrets still PLACEHOLDER.** The Lambda runs in mock mode whenever `STRIPE_SECRET_ARN` or `STRIPE_NETWORK_ID_ARN` is `PLACEHOLDER`. That's fine — mock mode still completes the full flow — but no real Stripe charges happen. Verify:
  ```bash
  aws secretsmanager get-secret-value --secret-id "$STRIPE_SECRET_ARN" \
    --query SecretString --output text | head -c 12
  aws secretsmanager get-secret-value --secret-id "$STRIPE_NETWORK_ID_ARN" \
    --query SecretString --output text | head -c 17
  ```
  If either prints `PLACEHOLDER`, go back to Chapter 2.
- **Buyer secret not baked.** If `BuyerStripeSecretArn` still points at `PLACEHOLDER`, Samurai returns the literal string `"PLACEHOLDER"` as the SPT (so mock mode on the Lambda side still accepts it). If one side has been updated to a real Stripe key while the other still has PLACEHOLDER, the payment step will fail — Samurai may send a placeholder token that ListingBot's real-mode validation rejects, or vice versa. Make sure both secrets are updated together. Owner: check with `aws secretsmanager get-secret-value --secret-id "$BUYER_STRIPE_SECRET_ARN" --query SecretString --output text | head -c 12`.
- **SPT creation failed at Stripe.** The `POST /v1/shared_payment/issued_tokens` call can fail if the buyer's sandbox doesn't have Agentic Commerce enabled, or the seller's `profile_test_` isn't reachable from the buyer's account. Check CloudWatch logs for `/aws/bedrock-agentcore/runtimes/samurai_*` — the `rpc` MPP log event shows the Stripe API endpoint and response; the `error` event shows the SPT creation failure reason.
- **30-second secret cache.** The Lambda and agent both re-read their secrets every 30 s; wait a moment after a `put-secret-value` and retry.
