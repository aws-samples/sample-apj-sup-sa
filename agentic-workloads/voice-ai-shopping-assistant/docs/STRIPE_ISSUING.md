# Stripe Issuing — `STRIPE_MODE` (simulation vs live)

The browser checkout pathway pays a card-only storefront with a **virtual card
issued by the x402→card broker** (`backend/tools/card_broker/`). The card is
issued via **Stripe Issuing**, and the card-network authorization (approve /
`insufficient_funds`) is performed by **Stripe** — both gated behind a single
flag so the demo runs anywhere and a fork can switch to real Stripe with one env
change.

## TL;DR

| `STRIPE_MODE` | Card issuance | Authorization | When to use |
|---------------|---------------|---------------|-------------|
| `simulation` (default) | Stripe-shaped **simulated** card minted locally (`4242…`, `sim_…` id) | Computed locally: approve iff `amount ≤ card limit`, else `insufficient_funds` | Default. Stripe Issuing is geo/approval-gated and unavailable on most accounts. |
| `live` | **Real** `stripe.issuing.Card.create` (virtual, per-auth limit = order total) | **Real** `stripe.test_helpers.issuing.Authorization.create` | A fork whose Stripe account has **Issuing activated**. |

In **both** modes the card's spending limit equals the order total, so the
approve/decline decision is identical — only the *issuer/authorizer* differs.
This means the end-to-end flow, the order lifecycle, and the
`declined_insufficient_funds` path all behave the same in simulation as in live.

## Why simulation is the default

Stripe **Issuing** is not self-serve for most accounts — it is region-limited
(US-first) and requires activation/approval. Calling it without activation
returns: *"Your account is not set up to use Issuing."* So the broker defaults
to a faithful simulation that mints a Stripe-test-shaped card and reproduces
Stripe's authorization decision, keeping the demo fully runnable.

## Switching a fork to real Stripe Issuing

1. Use a Stripe account that has **Issuing activated** in the target mode
   (`https://dashboard.stripe.com/issuing/overview`). Test mode is fine.
2. Store the secret key in Secrets Manager (already wired):
   ```
   aws secretsmanager create-secret --name /aisle/stripe/secret_key \
     --secret-string 'sk_test_...' --region ap-southeast-2
   ```
3. Set `STRIPE_MODE=live` on **both** Lambdas:
   - `aisle-card-broker`  (issues the card)
   - `aisle-place-order-async`  (authorizes the card)
   ```
   aws lambda update-function-configuration --function-name aisle-card-broker \
     --environment "Variables={...,STRIPE_MODE=live}" --region ap-southeast-2
   aws lambda update-function-configuration --function-name aisle-place-order-async \
     --environment "Variables={...,STRIPE_MODE=live}" --region ap-southeast-2
   ```
   (Or set `AISLE_STRIPE_MODE=live` before `cdk deploy` — see `tools-stack.ts`.)

No code change is required — the secret is read at runtime and the mode is read
per invocation. If `live` is set but Issuing still isn't activated, the broker
logs the Stripe error and falls back to simulation so the demo never hard-breaks.

## Where it's implemented

- `backend/tools/card_broker/handler.py` — `STRIPE_MODE`, `_issue_stripe_card`
  (live) vs simulated mint; persists `virtual_cards.stripe_card_id`.
- `backend/tools/place_order_async/handler.py` — `STRIPE_MODE`,
  `_stripe_authorize` (live test-helper authorization vs local decision);
  emits the `payment_authorized` event with `mode: live|simulation`.
- IAM: both Lambda roles have `secretsmanager:GetSecretValue` on
  `/aisle/stripe/secret_key`.
- The `stripe` SDK is bundled into both Lambda zips (pure Python; no Docker).
