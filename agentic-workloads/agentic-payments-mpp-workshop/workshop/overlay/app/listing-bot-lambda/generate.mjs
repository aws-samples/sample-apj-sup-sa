/**
 * POST /generate — WORKSHOP STARTER VERSION.
 *
 * Three-tier response (see Chapter 3):
 *   400 — bad JSON (no payment)
 *   422 — invalid input (no payment, RFC 7807 problem details)
 *   402 — missing / invalid MPP credential (Stripe SPT / fiat)
 *   200 — paid + validated → Bedrock Converse → listing JSON
 *
 * You will finish TODO 2 in this file. TODO 3 lives in `bedrock.mjs`.
 */
import { Mppx, stripe } from "mppx/server";

import { validateInput } from "./validation.mjs";
import { generateListing } from "./bedrock.mjs";
import { getSecret } from "./secrets.mjs";
import {
  isMockSecret,
  mockChallengeResponse,
  mockAccept200,
} from "./stripe-mock.mjs";

const LISTING_PRICE = process.env.LISTING_PRICE || "1.00";
const PROBLEM_BASE = "https://paymentauth.org/problems";

export async function handleGenerate(event) {
  let body;
  try {
    body = event.body ? JSON.parse(event.body) : {};
  } catch {
    return problemResponse(400, {
      type: `${PROBLEM_BASE}/malformed-json`,
      title: "Malformed JSON body",
      detail: "Request body must be valid JSON.",
    });
  }

  const { valid, errors } = validateInput(body);
  if (!valid) {
    return problemResponse(422, {
      type: `${PROBLEM_BASE}/invalid-input`,
      title: "Invalid input",
      detail: "The request failed schema validation. No payment was taken.",
      errors,
    });
  }

  const stripeKey = await getSecret(process.env.STRIPE_SECRET_ARN);
  const networkId = await getSecret(process.env.STRIPE_NETWORK_ID_ARN);
  const mppSecret = await getSecret(process.env.MPP_SECRET_ARN);
  if (!mppSecret) throw new Error("MPP_SECRET_ARN not configured");

  const request = eventToRequest(event);
  const mockMode = isMockSecret(stripeKey) || isMockSecret(networkId);

  // ═════════════════════════════════════════════════════════════════════════
  // TODO 2 — Register the Stripe SPT payment method with mppx/server.
  //
  //   Fill in the `methods` array with one entry: `stripe.charge({...})`.
  //   Required fields:
  //     networkId         — the Stripe Profile id (profile_test_* in mock
  //                         mode we pass the literal 'internal')
  //     paymentMethodTypes — ['card', 'link']
  //     secretKey          — the Stripe sandbox key (sk_test_* or the mock
  //                         placeholder in mock mode)
  //
  //   mppx/server takes care of paymentIntents.create on retry — you do NOT
  //   call Stripe directly from this file.
  //
  //   Docs: https://docs.stripe.com/payments/machine/mpp?mpp-method=spt
  // ═════════════════════════════════════════════════════════════════════════

  // const mppx = Mppx.create({
  // ...
  // secretKey: mppSecret,
  //});

  if (mockMode) {
    const authHeader = request.headers.get("authorization") || "";
    if (!authHeader.toLowerCase().startsWith("payment ")) {
      const challenge = await mppx.challenge.stripe.charge({
        amount: LISTING_PRICE,
        currency: "usd",
        decimals: 2,
        description: "Listing generation",
      });
      return mockChallengeResponse(challenge);
    }
  } else {
    const mppResult = await mppx.charge({
      amount: LISTING_PRICE,
      currency: "usd",
      decimals: 2,
      description: "Listing generation",
    })(request);

    if (mppResult.status === 402) {
      return await responseToLambda(mppResult.challenge);
    }

    const sessionId = resolveSessionId(event);

    let gen;
    try {
      gen = await generateListing(body);
    } catch (err) {
      console.error("Bedrock Converse failed", err);
      return problemResponse(502, {
        type: `${PROBLEM_BASE}/upstream-error`,
        title: "Upstream model error",
        detail: err?.message ?? "Bedrock Converse failed",
      });
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
    };
    return await responseToLambda(
      mppResult.withReceipt(
        new Response(JSON.stringify(responseBody), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
  }

  // ── Mock retry path ────────────────────────────────────────────────────
  const sessionId = resolveSessionId(event);

  let gen;
  try {
    gen = await generateListing(body);
  } catch (err) {
    console.error("Bedrock Converse failed", err);
    return problemResponse(502, {
      type: `${PROBLEM_BASE}/upstream-error`,
      title: "Upstream model error",
      detail: err?.message ?? "Bedrock Converse failed",
    });
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
  });
}

function resolveSessionId(event) {
  return (
    (event.headers || {})["x-session-id"] ||
    (event.headers || {})["X-Session-Id"] ||
    cryptoRandomSession()
  );
}

function eventToRequest(event) {
  const host =
    (event.headers || {}).host || (event.headers || {}).Host || "localhost";
  const proto =
    (event.headers || {})["x-forwarded-proto"] ||
    (event.headers || {})["X-Forwarded-Proto"] ||
    "https";
  const qs = event.queryStringParameters
    ? "?" + new URLSearchParams(event.queryStringParameters).toString()
    : "";
  const path =
    event.path || event.rawPath || event.requestContext?.http?.path || "/";
  const method =
    event.httpMethod || event.requestContext?.http?.method || "GET";
  const url = `${proto}://${host}${path}${qs}`;
  return new Request(url, {
    method,
    headers: new Headers(event.headers || {}),
    body: ["POST", "PUT", "PATCH"].includes(method) ? event.body || null : null,
  });
}

async function responseToLambda(response, extraHeaders = {}) {
  const body = await response.text();
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Expose-Headers":
      "WWW-Authenticate,Payment-Receipt,X-Payment-Intent-Id,x-amzn-remapped-www-authenticate",
    ...extraHeaders,
  };
  response.headers.forEach((v, k) => {
    headers[k] = v;
    if (k.toLowerCase() === "www-authenticate") {
      headers["x-amzn-remapped-www-authenticate"] = v;
    }
  });
  return { statusCode: response.status, headers, body };
}

function problemResponse(status, problem) {
  const body = { status, ...problem };
  return {
    statusCode: status,
    headers: {
      "Content-Type": "application/problem+json",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Expose-Headers":
        "WWW-Authenticate,Payment-Receipt,X-Payment-Intent-Id,x-amzn-remapped-www-authenticate",
    },
    body: JSON.stringify(body),
  };
}

function cryptoRandomSession() {
  const rand = () => Math.random().toString(36).slice(2, 12);
  return `sess_${Date.now().toString(36)}_${rand()}${rand()}`;
}
