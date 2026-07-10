/**
 * POST /generate — paid listing generation with a three-tier response:
 *
 *   1. Bad JSON         → 400 (no payment, no charge).
 *   2. Invalid input    → 422 RFC 7807 problem document (no payment).
 *   3. Missing payment  → 402 MPP challenge (Stripe SPT / fiat).
 *   4. Paid + valid     → 200 with { listing, usage, sessionId } plus a
 *                         Payment-Receipt header stamped by mppx.
 *
 * The payment primitive is orthogonal to validation, so we run validation
 * BEFORE gating payment — nobody pays for invalid input.
 *
 * Verification on retry is handled by mppx/server's stripe.charge: it reads
 * the SPT from the credential, calls Stripe to create a PaymentIntent
 * (hidden inside mppx), and returns a Receipt on success.
 *
 * TODO 2 (participant) lives in the `methods: [...]` argument to
 * `Mppx.create` — wiring `stripe.charge({ networkId, paymentMethodTypes,
 * secretKey })`. TODO 3 lives in bedrock.mjs.
 */
import { Mppx, stripe } from 'mppx/server'

import { validateInput } from './validation.mjs'
import { generateListing } from './bedrock.mjs'
import { getSecret } from './secrets.mjs'
import { isMockSecret, mockChallengeResponse, mockAccept200 } from './stripe-mock.mjs'

const LISTING_PRICE = process.env.LISTING_PRICE || '1.00'
const PROBLEM_BASE = 'https://paymentauth.org/problems'

export async function handleGenerate(event) {
  // ── Tier 1: parse JSON ──────────────────────────────────────────────────
  let body
  try {
    body = event.body ? JSON.parse(event.body) : {}
  } catch {
    return problemResponse(400, {
      type: `${PROBLEM_BASE}/malformed-json`,
      title: 'Malformed JSON body',
      detail: 'Request body must be valid JSON.',
    })
  }

  // ── Tier 2: validate (free — pre-payment gate) ──────────────────────────
  const { valid, errors } = validateInput(body)
  if (!valid) {
    return problemResponse(422, {
      type: `${PROBLEM_BASE}/invalid-input`,
      title: 'Invalid input',
      detail: 'The request failed schema validation. No payment was taken.',
      errors,
    })
  }

  // ── Tier 3: MPP gate (Stripe SPT) ───────────────────────────────────────
  const stripeKey = await getSecret(process.env.STRIPE_SECRET_ARN)
  const networkId = await getSecret(process.env.STRIPE_NETWORK_ID_ARN)
  const mppSecret = await getSecret(process.env.MPP_SECRET_ARN)
  if (!mppSecret) throw new Error('MPP_SECRET_ARN not configured')

  const request = eventToRequest(event)
  const mockMode = isMockSecret(stripeKey) || isMockSecret(networkId)

  const mppx = Mppx.create({
    methods: [stripe.charge({
      networkId: mockMode ? 'internal' : networkId,
      paymentMethodTypes: ['card', 'link'],
      secretKey: mockMode ? 'sk_test_mock' : stripeKey,
    })],
    secretKey: mppSecret,
  })

  if (mockMode) {
    const authHeader = request.headers.get('authorization') || ''
    if (!authHeader.toLowerCase().startsWith('payment ')) {
      const challenge = await mppx.challenge.stripe.charge({
        amount: LISTING_PRICE,
        currency: 'usd',
        decimals: 2,
        description: 'Listing generation',
      })
      return mockChallengeResponse(challenge)
    }
    // retry pass — accept placeholder credential, skip verify, generate
  } else {
    const mppResult = await mppx.charge({
      amount: LISTING_PRICE,
      currency: 'usd',
      decimals: 2,
      description: 'Listing generation',
    })(request)

    if (mppResult.status === 402) {
      return await responseToLambda(mppResult.challenge)
    }

    // ── Payment verified → generate listing via Bedrock Converse ─────────
    const sessionId = resolveSessionId(event)

    let gen
    try {
      gen = await generateListing(body)
    } catch (err) {
      console.error('Bedrock Converse failed', err)
      return problemResponse(502, {
        type: `${PROBLEM_BASE}/upstream-error`,
        title: 'Upstream model error',
        detail: err?.message ?? 'Bedrock Converse failed',
      })
    }

    const responseBody = {
      listing: gen.listing,
      usage: {
        inputTokens: gen.usage?.inputTokens,
        outputTokens: gen.usage?.outputTokens,
        modelId: gen.usage?.modelId,
        pricePaid: `$${LISTING_PRICE} USD`,
      },
      sessionId,
    }
    return await responseToLambda(
      mppResult.withReceipt(
        new Response(JSON.stringify(responseBody), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
  }

  // ── Mock retry path ────────────────────────────────────────────────────
  const sessionId = resolveSessionId(event)

  let gen
  try {
    gen = await generateListing(body)
  } catch (err) {
    console.error('Bedrock Converse failed', err)
    return problemResponse(502, {
      type: `${PROBLEM_BASE}/upstream-error`,
      title: 'Upstream model error',
      detail: err?.message ?? 'Bedrock Converse failed',
    })
  }

  return mockAccept200({
    listing: gen.listing,
    usage: {
      inputTokens: gen.usage?.inputTokens,
      outputTokens: gen.usage?.outputTokens,
      modelId: gen.usage?.modelId,
      pricePaid: `$${LISTING_PRICE} USD (mock — no Stripe call)`,
    },
    sessionId,
  })
}

// ── Web API / Lambda adapters ──────────────────────────────────────────────

function resolveSessionId(event) {
  return (
    (event.headers || {})['x-session-id'] ||
    (event.headers || {})['X-Session-Id'] ||
    cryptoRandomSession()
  )
}

function eventToRequest(event) {
  const host = (event.headers || {}).host || (event.headers || {}).Host || 'localhost'
  const proto =
    (event.headers || {})['x-forwarded-proto'] ||
    (event.headers || {})['X-Forwarded-Proto'] ||
    'https'
  const qs = event.queryStringParameters
    ? '?' + new URLSearchParams(event.queryStringParameters).toString()
    : ''
  const path = event.path || event.rawPath || event.requestContext?.http?.path || '/'
  const method = event.httpMethod || event.requestContext?.http?.method || 'GET'
  const url = `${proto}://${host}${path}${qs}`
  return new Request(url, {
    method,
    headers: new Headers(event.headers || {}),
    body: ['POST', 'PUT', 'PATCH'].includes(method) ? (event.body || null) : null,
  })
}

async function responseToLambda(response, extraHeaders = {}) {
  const body = await response.text()
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Expose-Headers':
      'WWW-Authenticate,Payment-Receipt,X-Payment-Intent-Id,x-amzn-remapped-www-authenticate',
    ...extraHeaders,
  }
  response.headers.forEach((v, k) => {
    headers[k] = v
    // API Gateway REST remaps the WWW-Authenticate response header. Mirror
    // it on the remapped name too so clients behind either hop can read it.
    if (k.toLowerCase() === 'www-authenticate') {
      headers['x-amzn-remapped-www-authenticate'] = v
    }
  })
  return { statusCode: response.status, headers, body }
}

function problemResponse(status, problem) {
  const body = { status, ...problem }
  return {
    statusCode: status,
    headers: {
      'Content-Type': 'application/problem+json',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Expose-Headers':
        'WWW-Authenticate,Payment-Receipt,X-Payment-Intent-Id,x-amzn-remapped-www-authenticate',
    },
    body: JSON.stringify(body),
  }
}

function cryptoRandomSession() {
  const rand = () => Math.random().toString(36).slice(2, 12)
  return `sess_${Date.now().toString(36)}_${rand()}${rand()}`
}
