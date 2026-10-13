import { Client } from "@opensearch-project/opensearch";
import { AwsSigv4Signer } from "@opensearch-project/opensearch/aws";
import { defaultProvider } from "@aws-sdk/credential-provider-node";

// GET-only transaction search. Fronted by API Gateway HTTP API + Cognito JWT
// authorizer (token already validated), runs INSIDE the Aurora VPC, and queries
// the PRIVATE OpenSearch Serverless collection via its VPC endpoint.
//
// MERCHANT ISOLATION (server-enforced): the merchant_id filter is taken from the
// VALIDATED JWT claim, never from the client. A merchant can only ever search
// their own transactions. Admins may search across tenants.
const REGION = process.env.AWS_REGION!;
const ENDPOINT = process.env.COLLECTION_ENDPOINT!; // https://xxx.aoss.amazonaws.com
const INDEX = process.env.INDEX_NAME || "transactions";

const client = new Client({
  ...AwsSigv4Signer({ region: REGION, service: "aoss", getCredentials: () => defaultProvider()() }),
  node: ENDPOINT,
});

function json(statusCode: number, body: unknown) {
  return { statusCode, headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function claimsOf(event: any) {
  const c = event.requestContext?.authorizer?.jwt?.claims ?? {};
  const raw = c["cognito:groups"];
  const groups: string[] = Array.isArray(raw)
    ? raw
    : typeof raw === "string"
    ? raw.replace(/[[\]]/g, "").split(/[\s,]+/).filter(Boolean)
    : [];
  return {
    isAdmin: groups.includes("admin"),
    isMerchant: groups.includes("merchant"),
    merchantId: (c["custom:merchant_id"] as string) || "",
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapHit(h: any) {
  const s = h._source ?? {};
  return {
    transactionId: s.transaction_id ?? h._id,
    merchantId: s.merchant_id,
    merchantName: s.merchant_name,
    customerName: s.customer_name,
    amount: s.amount,
    currency: s.currency,
    status: s.status,
    paymentMethod: s.payment_method,
    createdAt: s.created_at,
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const handler = async (event: any) => {
  try {
    const { isAdmin, isMerchant, merchantId } = claimsOf(event);
    if (!isAdmin && !isMerchant) return json(403, { message: "Not authorized" });
    if (isMerchant && !isAdmin && !merchantId)
      return json(403, { message: "No merchant tenant on token" });

    const params = event.queryStringParameters ?? {};
    const pathParams = event.pathParameters ?? {};

    // Tenant filter: merchants are FORCED to their own tenant. Admins see all
    // (optionally narrowed by ?merchant_id=).
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const tenantFilter: any[] = [];
    if (!isAdmin) tenantFilter.push({ term: { "merchant_id.keyword": merchantId } });
    else if (params.merchant_id)
      tenantFilter.push({ term: { "merchant_id.keyword": params.merchant_id } });

    const runSearch = async (body: unknown) => {
      try {
        const r = await client.search({ index: INDEX, body });
        return { hits: r.body?.hits?.hits ?? [], total: r.body?.hits?.total?.value ?? 0 };
      } catch (e: unknown) {
        // Index not created yet (zero-ETL still performing the initial load).
        const msg = JSON.stringify((e as { meta?: unknown; message?: string })?.message ?? e);
        if (msg.includes("index_not_found") || msg.includes("no such index"))
          return { hits: [], total: 0, notReady: true };
        throw e;
      }
    };

    // GET /transactions/{id}
    if (pathParams.id) {
      const { hits } = await runSearch({
        size: 1,
        query: { bool: { filter: [...tenantFilter, { ids: { values: [pathParams.id] } }] } },
      });
      if (hits.length === 0) return json(404, { message: "Transaction not found" });
      return json(200, { transaction: mapHit(hits[0]) });
    }

    // GET /transactions?q=&status=&limit=
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const filter: any[] = [...tenantFilter];
    if (params.status) filter.push({ term: { "status.keyword": params.status } });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const must: any[] = [];
    if (params.q)
      must.push({
        multi_match: {
          query: params.q,
          fields: ["customer_name", "transaction_id", "payment_method", "merchant_name", "currency"],
          type: "phrase_prefix",
        },
      });
    const size = Math.min(Math.max(parseInt(params.limit ?? "25", 10) || 25, 1), 100);
    const query =
      must.length || filter.length
        ? { bool: { ...(must.length ? { must } : {}), ...(filter.length ? { filter } : {}) } }
        : { match_all: {} };

    const { hits, total, notReady } = await runSearch({
      size,
      sort: [{ created_at: { order: "desc", unmapped_type: "long" } }],
      query,
    });
    return json(200, {
      transactions: hits.map(mapHit),
      total,
      ...(notReady ? { note: "Transaction index is still initializing (zero-ETL load in progress)." } : {}),
    });
  } catch (err) {
    console.error(err);
    return json(500, { message: err instanceof Error ? err.message : "Internal error" });
  }
};
