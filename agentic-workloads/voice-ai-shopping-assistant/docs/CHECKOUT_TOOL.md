# Checkout / `create_order` tool

How the agent turns a shopper's cart into a placed order. `create_order` is a
**router**: it looks up the merchant and takes exactly one of two pathways. The
card-only pathway is fulfilled asynchronously by an AgentCore Browser worker and
is fully observable in CloudWatch.

---

## Calling the tool

`create_order` (AgentCore Gateway MCP tool). Arguments:

| Arg | Required | Default | Meaning |
|-----|----------|---------|---------|
| `session_id` | yes | — | The shopper session whose cart is being ordered. |
| `merchant_id` | no | `aisle-grocery` | Which merchant to order from (see the `merchants` table). |
| `pickup_time` | no | — | Optional preferred pickup time (ISO-8601). |

Returns `{"data": {"order": Order}}` or `{"error": {code, message}}`. Follow up
with **`get_order_status`** (by `order_id` or `session_id`) for the live
timeline, payment audit, and browser screenshots.

---

## The two pathways

The `merchants` table (Aurora) is the source of truth. Each row has
`supports_x402` and an `endpoint`.

### A. x402-native merchant (`supports_x402 = true`) — e.g. `delivery-slot`
The agent pays the merchant **directly** via AgentCore Payments. No card, no
browser. Synchronous: the order comes back `placed` with a real `payment_id`.

```
create_order(merchant_id="delivery-slot")
  → POST merchant endpoint            → 402 (x402 requirements)
  → AgentCore ProcessPayment (x402)   → signed proof
  → POST merchant endpoint w/ proof   → 200 (service booked)
  → order status = placed             (payment_id recorded)
```
Use this for paid APIs / MCP tools / x402 stores. **Hero: AgentCore Payments.**

### B. card-only merchant (`supports_x402 = false`) — e.g. `aisle-grocery`
`create_order` records the order (`submitted`) and **enqueues** it to SQS. The
`place_order_async` worker fulfils it asynchronously by issuing a card and
driving the merchant's web checkout with the AgentCore Browser.

```
create_order(merchant_id="aisle-grocery")
  → record order (submitted) + enqueue {order_id} to SQS
  worker:
    balance_checked            (USDC wallet vs order total)
    card_purchase_started      → x402 ProcessPayment (real) → broker
    card_issued                (Stripe Issuing — see docs/STRIPE_ISSUING.md)
    payment_authorized         (Stripe authorization)
    browser_session_started    → reached_checkout → payment_details_entered
    order_placed               → status = placed
```
Use this for normal storefronts (groceries, retail, travel…). **Hero: AgentCore
Browser.**

---

## TEST_MODE (agent-facing result)

`create_order` has a `TEST_MODE` env flag (default **true**). When on, the tool
**always returns `status: "placed"`** to the agent immediately — so the shopper
is always told the order succeeded — even on the async browser pathway where
fulfillment is still running. The `orders` row keeps its **true** status and the
worker still executes and logs every real step to `order_events` / CloudWatch;
only the agent-facing response is forced to success. Set `TEST_MODE=false`
(`AISLE_TEST_MODE=false`) to report the real interim status (e.g. `submitted`
until the worker finishes, or a real decline).

## Simulation vs live (`STRIPE_MODE`)

The card pathway runs in one of two modes (default **simulation**). See
`docs/STRIPE_ISSUING.md` for the full Stripe details.

| | simulation (default) | live |
|---|---|---|
| Card issuance | Stripe-shaped simulated card (`4242…`, `sim_…`) | real `stripe.issuing.Card.create` |
| Authorization | always approves | real Stripe authorization |
| USDC balance shortfall (order > wallet) | **logged, then proceeds** | terminal `declined_insufficient_funds` |
| x402 crypto leg | a **real** but capped micropayment (`SIM_CHARGE_CAP_CENTS`, default $1) so the testnet wallet covers any order; card still funded to the full total | charges the full order total |
| **Final outcome** | **always `placed`** | genuine approve / decline |

> **Simulation always ends in success.** Intermediate steps that would fail in
> live mode (insufficient USDC, declined authorization) are recorded as events /
> CloudWatch logs but never stop the order. Flip a fork to real behaviour by
> setting `STRIPE_MODE=live` (and funding the wallet) — no code change.

---

## Observability (CloudWatch + DB)

Every lifecycle step is emitted to **two** places by the worker's `_emit`:

1. **CloudWatch** — a structured JSON line in the `aisle-place-order-async` log
   group, one per step:
   ```json
   {"checkout_event": "card_issued", "order_id": "…", "last4": "4242",
    "issuer": "stripe_simulation", "payment_id": "…"}
   ```
   Filter the log group on `checkout_event` to watch a checkout progress live.
2. **`order_events` table** — the same events, queryable; surfaced by
   `get_order_status` as the order timeline (plus payment audit + screenshots in
   `order_artifacts` / S3).

**Event sequence (card pathway, simulation):**
`order_created → enqueued_for_fulfillment → fulfillment_started →
balance_checked → [balance_shortfall_simulated] → card_purchase_started →
payment_processed → card_issued → payment_authorized → browser_session_started →
reached_checkout → payment_details_entered → order_placed`

**x402 pathway:** `order_created → payment_processed → order_placed`.

---

## Quick test

```bash
# x402 pathway (pays directly, no browser)
aws lambda invoke --function-name <CreateOrderFn> \
  --payload '{"session_id":"<sid>","merchant_id":"delivery-slot"}' out.json

# card pathway (async browser checkout); then poll status
aws lambda invoke --function-name <CreateOrderFn> \
  --payload '{"session_id":"<sid>","merchant_id":"aisle-grocery"}' out.json
aws lambda invoke --function-name aisle-get-order-status \
  --payload '{"order_id":"<order_id>"}' status.json

# watch the steps in CloudWatch
aws logs filter-log-events --log-group-name /aws/lambda/aisle-place-order-async \
  --filter-pattern checkout_event --region ap-southeast-2
```

## Implementation
- `backend/tools/create_order/handler.py` — router (`_load_merchant`, two-path branch).
- `backend/tools/place_order_async/handler.py` — async worker; `_emit` (DB + CloudWatch); `STRIPE_MODE` gating; capped sim charge (`SIM_CHARGE_CAP_CENTS`).
- `backend/tools/card_broker/handler.py` — x402→card broker (`charge_cents` vs `amount_cents`).
- `backend/tools/delivery_api/handler.py` — x402-native example merchant.
- `backend/db/schema.sql` — `merchants`, `order_events`, `order_artifacts`, `virtual_cards`.
