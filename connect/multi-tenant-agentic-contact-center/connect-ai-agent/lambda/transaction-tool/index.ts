import { Client } from "@opensearch-project/opensearch";
import { AwsSigv4Signer } from "@opensearch-project/opensearch/aws";
import { defaultProvider } from "@aws-sdk/credential-provider-node";

// ============================================================================
// AnyCompanyPay AI transaction-query TOOL (AgentCore Gateway Lambda target / MCP tool)
// ============================================================================
// This is the MCP tool the Amazon Connect AI agent calls to answer a merchant's
// questions about THEIR OWN transactions. It queries the private OpenSearch
// Serverless collection over SigV4 (service "aoss") from inside the Aurora VPC.
//
// TENANT ISOLATION (defense-in-depth, layer 2 of 2):
//   The AgentCore Gateway REQUEST INTERCEPTOR is the primary tenant gate — it
//   pins `merchant_id` from trusted session context and strips any model-supplied
//   value before this tool runs. This tool INDEPENDENTLY re-enforces the tenant:
//   it reads merchant_id ONLY from the trusted context the interceptor injects,
//   NEVER from the model-provided tool arguments, and ALWAYS applies the
//   merchant_id filter to every query. If no trusted tenant is present, it fails
//   closed (returns an error, never unfiltered data).
//
// The tool is READ-ONLY and data-minimized: it returns only the fields needed to
// answer transaction questions (id, amount, currency, status, method, date), not
// full customer PII.
// ----------------------------------------------------------------------------

const REGION = process.env.AWS_REGION!;
const ENDPOINT = process.env.COLLECTION_ENDPOINT!; // https://xxx.aoss.amazonaws.com
const INDEX = process.env.INDEX_NAME || "transactions";
// Reserved tool-argument key the Gateway request interceptor injects the
// trusted merchant_id into. The interceptor is the ONLY writer: it strips this
// key (and every model tenant key) from the model-supplied arguments before
// setting it from the trusted Connect contact, so a value present here can only
// have come from the interceptor — never the model. Must match the interceptor.
const TRUSTED_ARG_KEY = process.env.TRUSTED_ARG_KEY || "__trusted_merchant_id";

const client = new Client({
  ...AwsSigv4Signer({ region: REGION, service: "aoss", getCredentials: () => defaultProvider()() }),
  node: ENDPOINT,
});

const STATUS_SET = ["succeeded", "pending", "in_progress", "failed", "refunded", "authorized"];
const MAX_LIMIT = 25;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function extractTrustedMerchantId(event: any): string | null {
  // The trusted tenant comes ONLY from the Gateway request interceptor, which
  // resolves it from the Connect contact and injects it under TRUSTED_ARG_KEY.
  // The Gateway flattens the tool arguments onto the top level of the event, so
  // the interceptor's injected key lands at event[TRUSTED_ARG_KEY].
  //
  // That top-level slot is the ONLY place we read. The interceptor strips the
  // reserved key (and every tenant-looking key) from the TOP LEVEL of the model's
  // arguments before injecting, but it does not walk nested objects. Any nested
  // location (event.arguments / event.context / ...) is therefore fully
  // model-controlled — e.g. { arguments: { __trusted_merchant_id: "<other>" } } —
  // and MUST NOT be consulted, or the model could pick another tenant (and the
  // fail-closed path would be bypassed when the interceptor injects nothing).
  const v = event?.[TRUSTED_ARG_KEY];
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

// The model-supplied tool arguments. We use these ONLY for the question/filters
// (status, query text, limit) — never for the tenant.
// The AgentCore Gateway forwards the MCP tool arguments as the TOP-LEVEL event
// (e.g. { status: "failed", limit: 25, __trusted_merchant_id: "..." }), so when
// none of the well-known nested containers are present we treat the event itself
// as the argument bag.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function extractToolArgs(event: any): Record<string, unknown> {
  const nested =
    event?.arguments ?? event?.toolInput ?? event?.input ?? event?.parameters ?? event?.body;
  if (nested && typeof nested === "object") return nested as Record<string, unknown>;
  if (event && typeof event === "object") return event as Record<string, unknown>;
  return {};
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapHit(h: any) {
  const s = h._source ?? {};
  return {
    transactionId: s.transaction_id ?? h._id,
    amount: s.amount,
    currency: s.currency,
    status: s.status,
    paymentMethod: s.payment_method,
    createdAt: s.created_at,
    // NOTE: customer_name / merchant_name intentionally omitted (data minimization).
  };
}

// Standard MCP-style tool result. Text content is what the agent reasons over;
// structuredContent carries the machine-readable rows.
function toolResult(text: string, data?: unknown) {
  return {
    content: [{ type: "text", text }],
    ...(data !== undefined ? { structuredContent: data } : {}),
    isError: false,
  };
}
function toolError(message: string) {
  return { content: [{ type: "text", text: message }], isError: true };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const handler = async (event: any) => {
  try {
    // Log the invocation shape once so the interceptor->tool contract (where the
    // trusted merchant_id lands) is verifiable in CloudWatch. No secrets here —
    // just the tool args + injected tenant, all within this account's logs.
    try {
      console.log("tool event", JSON.stringify(event));
    } catch {
      /* noop */
    }

    // ---- TENANT GATE (fail closed) ----
    const merchantId = extractTrustedMerchantId(event);
    if (!merchantId) {
      // The interceptor should always inject this. If it's missing, refuse —
      // never run an unfiltered (cross-tenant) query.
      console.error("No trusted merchant_id in invocation context; refusing.");
      return toolError(
        "This request is missing a verified merchant identity, so I can't look up transactions."
      );
    }

    const args = extractToolArgs(event);
    // Tenant filter is ALWAYS applied and comes ONLY from the trusted context.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const filter: any[] = [{ term: { "merchant_id.keyword": merchantId } }];

    // Optional, model-supplied NARROWING filters (safe — they can only further
    // restrict within the tenant, never widen it).
    const status = typeof args.status === "string" ? args.status.toLowerCase() : undefined;
    if (status && STATUS_SET.includes(status)) filter.push({ term: { "status.keyword": status } });

    const transactionId =
      typeof args.transactionId === "string"
        ? args.transactionId
        : typeof args.transaction_id === "string"
        ? (args.transaction_id as string)
        : undefined;
    if (transactionId) filter.push({ ids: { values: [transactionId] } });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const must: any[] = [];
    const q = typeof args.query === "string" ? args.query : typeof args.q === "string" ? args.q : "";
    if (q.trim()) {
      must.push({
        multi_match: {
          query: q.trim(),
          fields: ["transaction_id", "payment_method", "currency", "status"],
          type: "phrase_prefix",
        },
      });
    }

    const rawLimit = Number(args.limit ?? 10);
    const size = Math.min(Math.max(Number.isFinite(rawLimit) ? rawLimit : 10, 1), MAX_LIMIT);

    const query = { bool: { filter, ...(must.length ? { must } : {}) } };

    // Primary query: hits only (mirrors the proven SearchApiFn shape). The
    // OpenSearch client's generics are stricter than our dynamic body, so treat
    // the request/response loosely.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let res: any;
    try {
      res = await client.search({
        index: INDEX,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        body: { size, sort: [{ created_at: { order: "desc", unmapped_type: "long" } }], query } as any,
      });
    } catch (e: unknown) {
      const msg = JSON.stringify((e as { message?: string })?.message ?? e);
      if (msg.includes("index_not_found") || msg.includes("no such index")) {
        return toolResult(
          "The transaction index isn't ready yet (data is still loading). Please try again shortly."
        );
      }
      throw e;
    }

    const hits = res.body?.hits?.hits ?? [];
    const total = res.body?.hits?.total?.value ?? hits.length;
    const rows = hits.map(mapHit);

    // Best-effort aggregates across ALL matches (size:0). If the collection
    // rejects the aggregation (e.g. field mapping), fall back to page-level sums
    // so the tool still answers "how many / how much".
    let byStatus: { status: string; count: number }[] = [];
    let totalAmount = 0;
    try {
      const agg = await client.search({
        index: INDEX,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        body: {
          size: 0,
          query,
          aggs: {
            by_status: { terms: { field: "status.keyword", size: STATUS_SET.length } },
            total_amount: { sum: { field: "amount" } },
          },
        } as any,
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      byStatus = (agg.body?.aggregations?.by_status?.buckets ?? []).map((b: any) => ({
        status: b.key,
        count: b.doc_count,
      }));
      totalAmount = agg.body?.aggregations?.total_amount?.value ?? 0;
    } catch {
      const counts: Record<string, number> = {};
      for (const r of rows) {
        totalAmount += Number(r.amount) || 0;
        if (r.status) counts[r.status] = (counts[r.status] ?? 0) + 1;
      }
      byStatus = Object.entries(counts).map(([status, count]) => ({ status, count }));
    }

    const summary =
      `Found ${total} transaction(s) for this merchant` +
      (status ? ` with status "${status}"` : "") +
      (q.trim() ? ` matching "${q.trim()}"` : "") +
      `. Showing ${rows.length}. Total amount (matched): ${Number(totalAmount).toFixed(2)}.`;

    return toolResult(summary, { merchantId, total, transactions: rows, byStatus, totalAmount });
  } catch (err) {
    console.error(err);
    return toolError("Sorry, I couldn't retrieve transactions right now.");
  }
};
