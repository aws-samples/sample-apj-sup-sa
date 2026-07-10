/**
 * Tool: generate_listing
 *
 * The paid tool. Calls ListingBot's /generate endpoint via MPP.
 *
 * Samurai acts as the BUYER. When the seller returns a 402 challenge,
 * the agent mints a fresh one-time SharedPaymentIssuedToken (SPT) scoped
 * to the seller's Stripe profile (read from the challenge), using a
 * pre-baked demo-buyer sandbox secret key stored in Secrets Manager.
 * The SPT is then attached via `mppx/client` on the retry.
 *
 * The buyer's sk_test_ never leaves the container — it's used to POST
 * /v1/shared_payment/issued_tokens against Stripe. This is the SAME
 * endpoint in sandbox and live mode (live mode just uses a sk_live_ key
 * and may require an additional human-approval step depending on the
 * issuing account's policy).
 *
 * Tool return value is returned as a plain object (Strands wraps it as a
 * toolResult). We keep the payload compact so the model can quote from it.
 */
import { tool } from '@strands-agents/sdk'
import { z } from 'zod'
import { Mppx, stripe } from 'mppx/client'
import { SecretsManagerClient, GetSecretValueCommand } from '@aws-sdk/client-secrets-manager'
import { logMppEvent } from '../mpp-logger.js'

const sm = new SecretsManagerClient({})

const STRIPE_API_VERSION = '2026-04-22.preview'
const STRIPE_ISSUE_SPT_URL = 'https://api.stripe.com/v1/shared_payment/issued_tokens'
const TEST_PAYMENT_METHOD = 'pm_card_visa'

// Pre-baked sandbox payment method id that the SPT is scoped against at
// mint time. In a production flow this would come from Stripe Elements or
// the agent's Link wallet; for the workshop we use Stripe's always-succeeds
// test card payment method.
const PAYMENT_METHOD_PLACEHOLDER = TEST_PAYMENT_METHOD

let buyerKeyPromise: Promise<string | null> | null = null
function getBuyerKey(): Promise<string | null> {
  if (!buyerKeyPromise) {
    buyerKeyPromise = (async () => {
      const arn = process.env.BUYER_STRIPE_SECRET_ARN
      if (!arn) throw new Error('BUYER_STRIPE_SECRET_ARN not set')
      const resp = await sm.send(new GetSecretValueCommand({ SecretId: arn }))
      const key = resp.SecretString ?? null
      if (!key || key === 'PLACEHOLDER') return null
      return key
    })()
  }
  return buyerKeyPromise
}

interface MintSptContext {
  amount: string            // in smallest currency unit (e.g. '100' for 100 cents)
  currency: string
  networkId: string         // seller's profile_test_*
  expiresAt: number         // unix seconds; if omitted by mppx we default to +7 days
}

// SPT default expiry when mppx/client doesn't compute one from the challenge.
// 7 days comfortably covers the single retry the agent is about to issue.
const SPT_DEFAULT_EXPIRY_SECONDS = 86400 * 7

/**
 * Calls Stripe's /v1/shared_payment/issued_tokens endpoint to create a
 * fresh one-time SharedPaymentIssuedToken scoped to the seller's profile.
 * Returns the spt_* string.
 *
 * If the buyer secret is PLACEHOLDER, returns the literal string
 * "PLACEHOLDER" so the Lambda's mock-mode branch (which accepts any
 * Payment header) still succeeds. This keeps the whole workshop demoable
 * before the owner sets up the buyer account.
 */
async function mintSpt(ctx: MintSptContext): Promise<string> {
  const buyerKey = await getBuyerKey()
  if (!buyerKey) {
    await logMppEvent({
      type: 'spt',
      label: '→ Skip SPT mint (buyer key is PLACEHOLDER, mock mode)',
      detail: { sellerProfile: ctx.networkId, amount: `${ctx.amount} ${ctx.currency}` },
    })
    return 'PLACEHOLDER'
  }

  const expiresAt = ctx.expiresAt > 0
    ? ctx.expiresAt
    : Math.floor(Date.now() / 1000) + SPT_DEFAULT_EXPIRY_SECONDS

  await logMppEvent({
    type: 'spt',
    label: '→ Mint SPT via Stripe shared_payment/issued_tokens',
    detail: {
      endpoint: STRIPE_ISSUE_SPT_URL,
      sellerProfile: ctx.networkId,
      amount: `${ctx.amount} ${ctx.currency}`,
      expiresAt: new Date(expiresAt * 1000).toISOString(),
      paymentMethod: TEST_PAYMENT_METHOD,
    },
  })

  const body = new URLSearchParams({
    payment_method: TEST_PAYMENT_METHOD,
    'seller_details[network_business_profile]': ctx.networkId,
    'usage_limits[max_amount]': ctx.amount,
    'usage_limits[currency]': ctx.currency,
    'usage_limits[expires_at]': String(expiresAt),
  })

  const resp = await fetch(STRIPE_ISSUE_SPT_URL, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${Buffer.from(`${buyerKey}:`).toString('base64')}`,
      'Stripe-Version': STRIPE_API_VERSION,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body,
  })

  if (!resp.ok) {
    const errBody = await resp.text().catch(() => '')
    let detail = errBody
    try { detail = (JSON.parse(errBody).error?.message as string) ?? errBody } catch {}
    throw new Error(`SPT mint failed (HTTP ${resp.status}): ${detail}`)
  }

  const data = await resp.json() as { id?: string }
  if (!data.id) throw new Error(`SPT mint returned no id: ${JSON.stringify(data)}`)
  return data.id
}

const nativeFetch = globalThis.fetch.bind(globalThis)

function parseChallenge(wwwAuth: string | null) {
  if (!wwwAuth) return null
  const m = wwwAuth.match(/request="([^"]+)"/)
  if (!m) return null
  try { return JSON.parse(Buffer.from(m[1], 'base64').toString('utf8')) } catch { return null }
}

function redactSpt(token: string): string {
  return `${token.slice(0, 9)}…  [${token.length} chars — MPP SPT credential]`
}

export const generateListingTool = tool({
  name: 'generate_listing',
  description:
    'Call the paid ListingBot /generate endpoint to produce a polished ' +
    'marketplace listing. Each call costs $1.00 USD: on receiving the ' +
    "server's 402 challenge, the tool mints a fresh one-time Shared " +
    "Payment Token against the demo buyer's Stripe account and attaches " +
    'it on retry. Pass the payload exactly matching the platform schema ' +
    'returned by discover_service. Returns the listing JSON plus usage metadata.',
  inputSchema: z.object({
    payload: z.record(z.string(), z.unknown())
      .describe(
        'Full payload matching the platform\'s schema from discover_service. ' +
        'Must include description + platform + any platform-required fields.',
      ),
  }),
  callback: async ({ payload }) => {
    const apiUrl = process.env.LISTING_BOT_API_URL
    if (!apiUrl) throw new Error('LISTING_BOT_API_URL not set')
    const url = `${apiUrl.replace(/\/$/, '')}/generate`

    const body = JSON.stringify(payload)

    const observableFetch: typeof fetch = async (input, init = {}) => {
      const initHeaders = init.headers as Record<string, string> | Headers | undefined
      const authHeader = getAuthHeader(initHeaders)

      if (!authHeader.startsWith('Payment')) {
        await logMppEvent({
          type: 'request',
          label: 'POST /generate',
          detail: safeJson(init.body),
        })
      } else {
        const token = authHeader.replace('Payment ', '')
        await logMppEvent({
          type: 'retry',
          label: 'POST /generate',
          detail: {
            body: safeJson(init.body),
            'Authorization header': `Payment ${redactSpt(token)}`,
          },
        })
      }

      let response = await nativeFetch(input as any, init)

      // API Gateway REST strips the `WWW-Authenticate` response header and
      // surfaces it as `x-amzn-remapped-www-authenticate` instead (an
      // undocumented behavior). mppx/client only looks for the canonical
      // header, so we rewrite it back here. See:
      // https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-mapping-template-reference.html
      if (response.status === 402 && !response.headers.get('www-authenticate')) {
        const remapped =
          response.headers.get('x-amzn-remapped-www-authenticate') ||
          response.headers.get('x-amzn-remapped-x-amzn-remapped-www-authenticate')
        if (remapped) {
          const headers = new Headers(response.headers)
          headers.set('www-authenticate', remapped)
          response = new Response(await response.clone().text(), {
            status: response.status,
            statusText: response.statusText,
            headers,
          })
        }
      }

      if (response.status === 402) {
        const piId = response.headers.get('x-payment-intent-id') ||
          response.headers.get('X-Payment-Intent-Id')
        const wwwAuth =
          response.headers.get('www-authenticate') ||
          response.headers.get('WWW-Authenticate') ||
          response.headers.get('x-amzn-remapped-www-authenticate') ||
          ''
        const challenge = parseChallenge(wwwAuth)
        const sellerProfile =
          challenge?.request?.methodDetails?.networkId ??
          challenge?.methodDetails?.networkId ??
          '(not in challenge)'
        const challengeAmount = challenge?.request?.amount
        const challengeCurrency = challenge?.request?.currency
        await logMppEvent({
          type: '402',
          label: '402 Payment Required',
          detail: {
            method: challenge?.method ?? 'stripe',
            amount: challengeAmount != null && challengeCurrency
              ? `${challengeAmount} ${String(challengeCurrency).toUpperCase()} (smallest unit)`
              : '$1.00 USD',
            sellerProfile,
            'Stripe PaymentIntent': piId ?? '(created on retry)',
          },
        })
      } else if (response.status === 422) {
        const cloned = response.clone()
        const data = await cloned.json().catch(() => null)
        await logMppEvent({
          type: 'error',
          label: '422 Invalid input (pre-payment)',
          detail: data,
        })
      } else if (response.ok && authHeader.startsWith('Payment')) {
        const cloned = response.clone()
        const data = await cloned.json().catch(() => null)
        const paymentIntentId = response.headers.get('x-payment-intent-id') ?? undefined
        await logMppEvent({
          type: '200',
          label: '200 OK — listing generated',
          detail: { paymentIntentId, ...data },
        })
      }
      return response
    }

    const client = Mppx.create({
      methods: [stripe({
        paymentMethod: PAYMENT_METHOD_PLACEHOLDER,
        createToken: async ({ amount, currency, networkId, expiresAt }) => {
          if (!networkId) throw new Error('seller networkId missing from 402 challenge')
          return mintSpt({ amount, currency, networkId, expiresAt })
        },
      })],
      polyfill: false,
      fetch: observableFetch,
    })

    try {
      const res = await client.fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      })

      const text = await res.text()
      let data: any
      try { data = text ? JSON.parse(text) : {} } catch { data = { raw: text } }

      if (!res.ok) {
        const msg = data?.detail || data?.error || `ListingBot /generate failed: HTTP ${res.status}`
        await logMppEvent({ type: 'error', label: `tool error: ${msg}`, detail: data })
        throw new Error(msg)
      }
      return data
    } catch (err: any) {
      // mppx/client throws when it can't complete the payment (SPT mint
      // failure, SPT rejected by Stripe, seller profile misconfigured, etc).
      // Surface the real message so the agent can report it honestly
      // instead of hallucinating a "network snag".
      const msg = err?.message ?? String(err)
      console.error('[generate_listing] mppx/client threw:', msg, err?.stack)
      await logMppEvent({
        type: 'error',
        label: `MPP payment failed: ${msg}`,
        detail: { message: msg, stack: err?.stack?.split('\n').slice(0, 4) },
      })
      throw new Error(`Payment failed: ${msg}`)
    }
  },
})

function getAuthHeader(headers: Record<string, string> | Headers | undefined): string {
  if (!headers) return ''
  if (headers instanceof Headers) return headers.get('authorization') || headers.get('Authorization') || ''
  return (headers as Record<string, string>)['Authorization'] ?? (headers as Record<string, string>)['authorization'] ?? ''
}

function safeJson(body: BodyInit | null | undefined) {
  if (typeof body !== 'string') return null
  try { return JSON.parse(body) } catch { return null }
}
