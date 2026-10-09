// ============================================================================
// AnyCompanyPay AgentCore Gateway REQUEST INTERCEPTOR (tenant gate)
// ============================================================================
// Runs INSIDE the AgentCore Gateway request path, BEFORE the tool target is
// invoked. It follows the AgentCore Gateway REQUEST-interceptor contract:
//
//   INPUT : event.mcp.gatewayRequest.body  = the JSON-RPC MCP request
//           (body.method is "initialize" | "tools/list" | "tools/call" | ...;
//            for tools/call, body.params = { name, arguments }).
//   OUTPUT: { "interceptorOutputVersion": "1.0",
//             "mcp": { "transformedGatewayRequest": { "body": <request body> } } }
//   (RESPONSE-interceptor invocations carry mcp.gatewayResponse instead; we pass
//    those straight through too.)
//
// CRITICAL: the interceptor runs on EVERY gateway request, including the
// `tools/list` discovery call that Amazon Connect makes to enumerate tools.
// It must therefore PASS THROUGH non-tool-call methods unchanged and always
// return the exact wrapper shape above — otherwise the gateway reports
// "Received invalid response from interceptor" and discovery yields no tools
// (the tool namespace never appears in Connect AI Agent Designer).
//
// Tenant isolation job (only on tools/call):
//   - STRIP any merchant_id / merchant / tenant the model put in the tool
//     arguments (prompt-injection defense).
//   - INJECT the trusted merchant_id — resolved from the Connect contact
//     attributes via the x-amz-connect-contact-id header — under the reserved
//     top-level argument key TRUSTED_ARG_KEY.
//   - The tool Lambda independently re-applies the tenant filter, reading the
//     tenant ONLY from that top-level key (defense-in-depth), and fails closed
//     if absent.
// ----------------------------------------------------------------------------

import { ConnectClient, GetContactAttributesCommand } from "@aws-sdk/client-connect";

const REGION = process.env.AWS_REGION!; // always set by the Lambda runtime
// Contact-attribute key ChatApiFn stamps the trusted tenant into.
const MERCHANT_ATTR_KEY = process.env.MERCHANT_ATTR_KEY || "merchant_id";
// Reserved tool-argument key we inject the trusted merchant_id into. The tool
// reads the tenant ONLY from this key (never from ordinary model args). Must
// match TRUSTED_ARG_KEY in the tool Lambda.
const TRUSTED_ARG_KEY = process.env.TRUSTED_ARG_KEY || "__trusted_merchant_id";

// Model-supplied keys we always strip (prompt-injection defense). Includes the
// reserved key so the model can never pre-seed a trusted-looking value.
const TENANT_ARG_KEYS = [
  "merchant_id",
  "merchantId",
  "merchant",
  "tenant",
  "tenantId",
  TRUSTED_ARG_KEY,
];

const OUTPUT_VERSION = "1.0";

const connect = new ConnectClient({ region: REGION });

// Headers arrive lower-cased from the Gateway, but read case-insensitively to be
// safe across changes.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function header(headers: any, name: string): string | undefined {
  if (!headers || typeof headers !== "object") return undefined;
  const want = name.toLowerCase();
  for (const k of Object.keys(headers)) {
    if (k.toLowerCase() === want) {
      const v = headers[k];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
  }
  return undefined;
}

// Resolve the trusted merchant_id for this contact from the Connect contact
// attributes (stamped by ChatApiFn from the merchant's validated Cognito JWT at
// chat start). This is the JWT -> contact-attribute -> tool link (Hop 2). It does
// NOT depend on the ephemeral Q-in-Connect session or the session seeder.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function resolveTrustedMerchantId(headers: any): Promise<string | null> {
  const contactId = header(headers, "x-amz-connect-contact-id");
  const instanceArn = header(headers, "x-amz-connect-instance-arn");
  const instanceId = instanceArn ? instanceArn.split("/").pop() : undefined;
  if (!contactId || !instanceId) {
    console.warn("interceptor: missing contact-id/instance-arn headers; cannot resolve tenant.");
    return null;
  }
  try {
    const res = await connect.send(
      new GetContactAttributesCommand({ InstanceId: instanceId, InitialContactId: contactId })
    );
    const v = res.Attributes?.[MERCHANT_ATTR_KEY];
    if (typeof v === "string" && v.trim()) return v.trim();
    console.warn(`interceptor: contact ${contactId} has no '${MERCHANT_ATTR_KEY}' attribute.`);
    return null;
  } catch (err) {
    console.error("interceptor: GetContactAttributes failed", err);
    return null;
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function passThroughRequest(body: any) {
  return {
    interceptorOutputVersion: OUTPUT_VERSION,
    mcp: { transformedGatewayRequest: { body } },
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function passThroughResponse(gatewayResponse: any) {
  return {
    interceptorOutputVersion: OUTPUT_VERSION,
    mcp: {
      transformedGatewayResponse: {
        body: gatewayResponse?.body ?? {},
        statusCode: gatewayResponse?.statusCode ?? 200,
      },
    },
  };
}

function stripModelTenant(args: Record<string, unknown>): Record<string, unknown> {
  const cleaned = { ...args };
  for (const k of TENANT_ARG_KEYS) {
    if (k in cleaned) delete cleaned[k];
  }
  return cleaned;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const handler = async (event: any) => {
  const mcp = event?.mcp ?? {};

  // RESPONSE-interceptor invocation: pass the response through unchanged.
  if (mcp.gatewayResponse != null) {
    return passThroughResponse(mcp.gatewayResponse);
  }

  // REQUEST-interceptor invocation.
  const gatewayRequest = mcp.gatewayRequest ?? {};
  const body = gatewayRequest.body ?? {};
  const method: string = body?.method ?? "unknown";

  // Everything that is NOT an actual tool invocation (initialize, tools/list,
  // notifications, ping, ...) passes through unchanged. This is what keeps
  // discovery working.
  if (method !== "tools/call") {
    return passThroughRequest(body);
  }

  // tools/call: (1) strip any model-supplied tenant keys (incl. the reserved
  // key) as prompt-injection defense, then (2) resolve the TRUSTED merchant_id
  // from the Connect contact and inject it under the reserved key. The tool
  // reads the tenant ONLY from that reserved key and fails closed if it's absent.
  const params = (body.params ?? {}) as Record<string, unknown>;
  const args = (params.arguments ?? {}) as Record<string, unknown>;
  const cleanedArgs = stripModelTenant(args);

  const headers = gatewayRequest.headers ?? {};
  const trustedMerchantId = await resolveTrustedMerchantId(headers);
  if (trustedMerchantId) {
    cleanedArgs[TRUSTED_ARG_KEY] = trustedMerchantId;
    console.log(`interceptor: injected trusted tenant for tools/call (${trustedMerchantId}).`);
  } else {
    // Fail closed: leave the reserved key absent so the tool refuses to run an
    // unfiltered (cross-tenant) query.
    console.warn("interceptor: no trusted tenant resolved; tool will fail closed.");
  }

  const newBody = { ...body, params: { ...params, arguments: cleanedArgs } };
  return passThroughRequest(newBody);
};
