---
title: "Wrap-Up"
weight: 100
---

### What You Have Learnt

- **A paid API that autonomous agents can discover, pay, and call.** ListingBot now speaks the full MPP contract — free discovery via `/openapi.json`, free validation via `/validate`, and a paid `/generate` endpoint that only takes payment for requests that would actually do work.
- **A Stripe MPP gate over SPT** with the full 402 → retry dance: mppx-generated HMAC-bound challenges per request, and server-side PaymentIntent creation from a Shared Payment Token scoped to your Stripe Profile.
- **Bedrock Converse (Sonnet 4.6) as the listing generator** — one LLM call, not an agent loop. The service doesn't need agent-shaped logic; the caller does.
- **A Strands TS user agent on AgentCore Runtime** with Memory, invoked directly from the browser via Cognito Identity Pool + SigV4. This is how your future customers — real autonomous agents — will talk to your service.
- **An MPP specific debug panel** reading the DynamoDB event log with Cognito-scoped credentials (no backend-for-frontend Lambda) so you can watch the whole handshake in real time.

### Stretch Ideas

- **Add a pricing tier.** Change `LISTING_PRICE` to `0.05` and retry — Samurai still pays without any client change (price is server-owned).
- **Add a new platform** to `rules.json` — e.g. other Agent or API services. No code changes on Samurai's side because it discovers schemas dynamically via `/openapi.json`.
- **Replace Bedrock Converse with an agent loop** in the Lambda (with its own tools for image search or competitor lookup). Samurai does not need to know — your internal implementation is invisible to the caller.
- **Already have a service that is ready to add monetizations?** Read about [MPP Security](https://mpp.dev/advanced/security) and explores [registries](https://mpp.dev/advanced/discovery)for agents to discover your services and [Stripe Account Checklist](https://docs.stripe.com/get-started/account/checklist). 


### Clean Up

:::alert{type="info"}
**No action required.** This workshop runs in a Workshop Studio sandbox account that is reclaimed automatically at event end. All CloudFormation stacks, Lambda functions, DynamoDB tables, Secrets Manager entries, ECR images, and CloudWatch log groups are destroyed when the sandbox is deleted. You do not need to run any cleanup commands.
:::

The CloudFormation stacks are still designed to delete cleanly (retry-safe secrets, auto-emptying S3 bucket) so that Workshop Studio can re-deploy into the same sandbox if needed.

### References

- Stripe MPP implementation with SPT — https://docs.stripe.com/payments/machine/mpp?mpp-method=spt
- MPP - https://mpp.dev/ 
- `mppx`library - https://mpp.dev/sdk/typescript/
- Bedrock AgentCore Runtime — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html
- Strands Agents SDK (TS) — https://github.com/strands-agents/sdk-typescript
