import { randomBytes } from "crypto";
import { SecretsManagerClient, GetSecretValueCommand } from "@aws-sdk/client-secrets-manager";
import { Pool } from "pg";
import {
  ConnectCasesClient,
  ListFieldsCommand,
  ListTemplatesCommand,
  CreateCaseCommand,
  CreateRelatedItemCommand,
} from "@aws-sdk/client-connectcases";

// Commerce write API. Fronted by API Gateway HTTP API + Cognito JWT authorizer
// (token already validated), runs INSIDE the Aurora VPC, talks to Postgres via
// `pg`. Models refunds + disputes as their own first-class resources:
//
//   POST /transactions            create a payment (merchant_id forced from JWT)
//   GET  /refunds                 list refunds for the tenant
//   GET  /refunds/{id}            one refund
//   POST /refunds                 create a refund (idempotent; amount <= remaining
//                                  refundable; REJECTED if the payment is disputed)
//   GET  /disputes                list disputes for the tenant
//   GET  /disputes/{id}           one dispute
//   POST /disputes                open a dispute (demo) -> auto-files a tenant-tagged
//                                  Amazon Connect Case and links case_id
//   POST /disputes/{id}/evidence  submit evidence -> moves dispute to under_review
//
// TENANT ISOLATION (server-enforced): merchant_id always comes from the VALIDATED
// JWT claim for merchant callers, never from the client. Admins may act across
// tenants (and must name the tenant when creating a transaction).
const REGION = process.env.AWS_REGION!;
const DB_HOST = process.env.DB_HOST!;
const DB_PORT = Number(process.env.DB_PORT || 5432);
const DB_NAME = process.env.DB_NAME!;
const DB_SECRET_ARN = process.env.DB_SECRET_ARN!;
const CASES_DOMAIN_ID = process.env.CASES_DOMAIN_ID || "";
const CASES_TEMPLATE_NAME = process.env.CASES_TEMPLATE_NAME || "AnyCompanyPaySupport";

const PAYMENT_STATUSES = ["succeeded", "pending", "in_progress", "failed", "refunded", "authorized"];
const CURRENCIES = ["SGD", "USD"];
// A payment can only be refunded from these states.
const REFUNDABLE_STATUSES = ["succeeded", "refunded"];
// Disputes in these states block a refund (money is contested / already lost).
const REFUND_BLOCKING_DISPUTE = ["needs_response", "under_review", "lost"];

const sm = new SecretsManagerClient({ region: REGION });
const cases = new ConnectCasesClient({ region: REGION });

let pool: Pool | null = null;
async function getPool(): Promise<Pool> {
  if (pool) return pool;
  const raw = (await sm.send(new GetSecretValueCommand({ SecretId: DB_SECRET_ARN }))).SecretString!;
  const secret = JSON.parse(raw) as { username: string; password: string };
  pool = new Pool({
    host: DB_HOST,
    port: DB_PORT,
    database: DB_NAME,
    user: secret.username,
    password: secret.password,
    ssl: { rejectUnauthorized: false }, // Aurora presents an AWS-managed cert
    max: 2,
    connectionTimeoutMillis: 15000,
    idleTimeoutMillis: 30000,
  });
  return pool;
}

// Cases field-name -> fieldId and template-name -> templateId, resolved once and
// cached across warm invocations so nothing is hardcoded to generated IDs.
let fieldCache: Record<string, string> | null = null;
async function resolveFields(): Promise<Record<string, string>> {
  if (fieldCache) return fieldCache;
  const out: Record<string, string> = {};
  let next: string | undefined;
  do {
    const r = await cases.send(
      new ListFieldsCommand({ domainId: CASES_DOMAIN_ID, maxResults: 50, nextToken: next })
    );
    for (const f of r.fields ?? []) if (f.name && f.fieldId) out[f.name] = f.fieldId;
    next = r.nextToken;
  } while (next);
  fieldCache = out;
  return out;
}
let templateCache: string | null = null;
async function resolveTemplate(): Promise<string> {
  if (templateCache) return templateCache;
  let next: string | undefined;
  do {
    const r = await cases.send(
      new ListTemplatesCommand({ domainId: CASES_DOMAIN_ID, maxResults: 50, nextToken: next })
    );
    const t = (r.templates ?? []).find((x) => x.name === CASES_TEMPLATE_NAME);
    if (t?.templateId) {
      templateCache = t.templateId;
      return templateCache;
    }
    next = r.nextToken;
  } while (next);
  throw new Error(`Cases template "${CASES_TEMPLATE_NAME}" not found in domain ${CASES_DOMAIN_ID}`);
}

function json(statusCode: number, body: unknown) {
  return { statusCode, headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
}
const sv = (v: string | undefined) => ({ stringValue: v ?? "" });
const money = (v: unknown) => Number(Number(v).toFixed(2));

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
    merchantName: (c["custom:merchant_name"] as string) || "",
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapRefund(r: any) {
  return {
    refundId: r.refund_id,
    transactionId: r.transaction_id,
    merchantId: r.merchant_id,
    amount: Number(r.amount),
    currency: r.currency,
    reason: r.reason,
    status: r.status,
    notes: r.notes,
    idempotencyKey: r.idempotency_key,
    createdAt: r.created_at,
  };
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapDispute(r: any) {
  return {
    disputeId: r.dispute_id,
    transactionId: r.transaction_id,
    merchantId: r.merchant_id,
    reason: r.reason,
    status: r.status,
    amount: Number(r.amount),
    currency: r.currency,
    evidence: r.evidence,
    dueBy: r.due_by,
    caseId: r.case_id,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

// File an Amazon Connect Case for an opened dispute. Best-effort: a failure here
// must not lose the dispute record, so the caller logs and continues.
async function createDisputeCase(d: {
  disputeId: string;
  transactionId: string;
  merchantId: string;
  merchantName: string;
  reason: string;
  amount: number;
  currency: string;
  dueBy: string;
}): Promise<string | null> {
  if (!CASES_DOMAIN_ID) return null;
  const [fields, templateId] = await Promise.all([resolveFields(), resolveTemplate()]);
  const title = `Dispute ${d.disputeId} (${d.merchantName || d.merchantId})`;
  const summary =
    `Chargeback/dispute on payment ${d.transactionId}. Reason: ${d.reason}. ` +
    `Amount: ${d.amount} ${d.currency}. Respond with evidence by ${d.dueBy}.`;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const caseFields: any[] = [{ id: "title", value: sv(title) }];
  if (fields["summary"]) caseFields.push({ id: fields["summary"], value: sv(summary) });
  if (fields["priority"]) caseFields.push({ id: fields["priority"], value: sv("High") });
  if (fields["case_status"]) caseFields.push({ id: fields["case_status"], value: sv("Open") });
  if (fields["merchant"]) caseFields.push({ id: fields["merchant"], value: sv(d.merchantName) });
  if (fields["merchant_id"]) caseFields.push({ id: fields["merchant_id"], value: sv(d.merchantId) });

  const res = await cases.send(
    new CreateCaseCommand({ domainId: CASES_DOMAIN_ID, templateId, fields: caseFields })
  );
  const caseId = res.caseId!;
  // Attach the dispute detail as the first comment (best-effort within best-effort).
  try {
    await cases.send(
      new CreateRelatedItemCommand({
        domainId: CASES_DOMAIN_ID,
        caseId,
        type: "Comment",
        content: { comment: { body: summary.slice(0, 3000), contentType: "Text/Plain" } },
      })
    );
  } catch (e) {
    console.error("dispute case comment failed (non-fatal)", e);
  }
  return caseId;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const handler = async (event: any) => {
  try {
    const { isAdmin, isMerchant, merchantId, merchantName } = claimsOf(event);
    if (!isAdmin && !isMerchant) return json(403, { message: "Not authorized" });
    if (isMerchant && !isAdmin && !merchantId) return json(403, { message: "No merchant tenant on token" });

    const method: string = event.requestContext?.http?.method ?? "GET";
    const rawPath: string = event.rawPath ?? "/";
    const pathParams = event.pathParameters ?? {};
    const qs = event.queryStringParameters ?? {};
    const body = event.body ? JSON.parse(event.body) : {};
    const db = await getPool();

    // Scope helper: merchants are pinned to their tenant; admins may filter by
    // ?merchant_id (or see everything). `start` is the 1-based bind-param index
    // the clause's placeholder should use, so callers can prepend other params.
    const tenantScopeAt = (start: number): { clause: string; params: string[] } => {
      if (!isAdmin) return { clause: `merchant_id = $${start}`, params: [merchantId] };
      if (qs.merchant_id) return { clause: `merchant_id = $${start}`, params: [qs.merchant_id] };
      return { clause: "TRUE", params: [] };
    };

    // ---------------------------------------------------------------- transactions
    // POST /transactions — create a payment.
    if (method === "POST" && rawPath.endsWith("/transactions")) {
      const amount = money(body.amount);
      if (!amount || amount <= 0) return json(400, { message: "amount must be a positive number" });
      const currency = String(body.currency || "SGD").toUpperCase();
      if (!CURRENCIES.includes(currency)) return json(400, { message: `currency must be one of ${CURRENCIES.join(", ")}` });
      const status = String(body.status || "succeeded");
      if (!PAYMENT_STATUSES.includes(status)) return json(400, { message: `status must be one of ${PAYMENT_STATUSES.join(", ")}` });
      const paymentMethod = String(body.paymentMethod || "card");

      // Tenant fields: merchants forced from JWT; admins must name the tenant.
      const mId = isAdmin ? String(body.merchantId || "") : merchantId;
      const mName = isAdmin ? String(body.merchantName || body.merchant || "") : merchantName;
      if (isAdmin && !mId) return json(400, { message: "merchantId is required for admin-created transactions" });

      const id = "txn_" + randomBytes(12).toString("hex");
      const customer = String(body.customerName || "").slice(0, 120) || null;
      const inserted = await db.query(
        `INSERT INTO transactions
           (transaction_id, merchant_id, merchant_name, customer_name, amount, currency, status, payment_method)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
         RETURNING *`,
        [id, mId, mName, customer, amount, currency, status, paymentMethod]
      );
      const t = inserted.rows[0];
      return json(201, {
        transaction: {
          transactionId: t.transaction_id,
          merchantId: t.merchant_id,
          merchantName: t.merchant_name,
          customerName: t.customer_name,
          amount: Number(t.amount),
          currency: t.currency,
          status: t.status,
          paymentMethod: t.payment_method,
          createdAt: t.created_at,
        },
      });
    }

    // ---------------------------------------------------------------- refunds
    // GET /refunds/{id}
    if (method === "GET" && pathParams.id && rawPath.includes("/refunds/")) {
      const scope = tenantScopeAt(1);
      const params = [...scope.params, pathParams.id];
      const r = await db.query(
        `SELECT * FROM refunds WHERE ${scope.clause} AND refund_id = $${params.length}`,
        params
      );
      if (r.rows.length === 0) return json(404, { message: "Refund not found" });
      return json(200, { refund: mapRefund(r.rows[0]) });
    }

    // GET /refunds
    if (method === "GET" && rawPath.endsWith("/refunds")) {
      const scope = tenantScopeAt(1);
      const params = [...scope.params];
      let clause = scope.clause;
      if (qs.transaction_id) {
        params.push(qs.transaction_id);
        clause += ` AND transaction_id = $${params.length}`;
      }
      const r = await db.query(
        `SELECT * FROM refunds WHERE ${clause} ORDER BY created_at DESC LIMIT 200`,
        params
      );
      return json(200, { refunds: r.rows.map(mapRefund) });
    }

    // POST /refunds — create a refund with idempotency + business rules.
    if (method === "POST" && rawPath.endsWith("/refunds")) {
      const transactionId = String(body.transactionId || "");
      if (!transactionId) return json(400, { message: "transactionId is required" });
      const idemKey: string | null = body.idempotencyKey ? String(body.idempotencyKey) : null;

      // Idempotency: same (tenant, key) returns the original refund.
      if (idemKey) {
        const existing = await db.query(
          `SELECT * FROM refunds WHERE merchant_id = $1 AND idempotency_key = $2`,
          [isAdmin ? body.merchantId || "" : merchantId, idemKey]
        );
        if (existing.rows.length) return json(200, { refund: mapRefund(existing.rows[0]), idempotent: true });
      }

      // Load the payment (tenant-scoped for merchants).
      const txScope = isAdmin ? { clause: "TRUE", params: [] as string[] } : { clause: "merchant_id = $2", params: [merchantId] };
      const txRes = await db.query(
        `SELECT * FROM transactions WHERE transaction_id = $1 AND ${txScope.clause}`,
        [transactionId, ...txScope.params]
      );
      if (txRes.rows.length === 0) return json(404, { message: "Transaction not found" });
      const tx = txRes.rows[0];
      const txMerchantId = tx.merchant_id as string;

      if (!REFUNDABLE_STATUSES.includes(tx.status))
        return json(409, { message: `Payment status "${tx.status}" is not refundable` });

      // Rule: cannot refund a disputed payment (double-loss). Blocked while a
      // dispute is open/under review, or if the merchant already lost it.
      const disp = await db.query(
        `SELECT status FROM disputes WHERE transaction_id = $1 AND status = ANY($2::text[])`,
        [transactionId, REFUND_BLOCKING_DISPUTE]
      );
      if (disp.rows.length)
        return json(409, {
          message: "Payment has an active/lost dispute and cannot be refunded",
          disputeStatus: disp.rows[0].status,
        });

      // Amount: default to the full remaining refundable balance.
      const priorRes = await db.query(
        `SELECT COALESCE(SUM(amount),0)::numeric AS refunded FROM refunds
           WHERE transaction_id = $1 AND status <> 'failed'`,
        [transactionId]
      );
      const alreadyRefunded = money(priorRes.rows[0].refunded);
      const remaining = money(Number(tx.amount) - alreadyRefunded);
      if (remaining <= 0) return json(409, { message: "Payment is already fully refunded" });
      const amount = body.amount === undefined || body.amount === null ? remaining : money(body.amount);
      if (amount <= 0) return json(400, { message: "amount must be a positive number" });
      if (amount > remaining)
        return json(409, { message: `amount exceeds refundable balance (${remaining} ${tx.currency})`, remaining });

      const reason = body.reason ? String(body.reason) : "requested_by_customer";
      const notes = body.notes ? String(body.notes).slice(0, 500) : null;
      const id = "ref_" + randomBytes(10).toString("hex");
      let refundRow;
      try {
        const ins = await db.query(
          `INSERT INTO refunds
             (refund_id, transaction_id, merchant_id, amount, currency, reason, status, notes, idempotency_key)
           VALUES ($1,$2,$3,$4,$5,$6,'succeeded',$7,$8)
           RETURNING *`,
          [id, transactionId, txMerchantId, amount, tx.currency, reason, notes, idemKey]
        );
        refundRow = ins.rows[0];
      } catch (e: unknown) {
        // Unique idempotency collision under a race — return the winner.
        if ((e as { code?: string }).code === "23505" && idemKey) {
          const w = await db.query(`SELECT * FROM refunds WHERE merchant_id = $1 AND idempotency_key = $2`, [
            txMerchantId,
            idemKey,
          ]);
          if (w.rows.length) return json(200, { refund: mapRefund(w.rows[0]), idempotent: true });
        }
        throw e;
      }

      // If fully refunded now, reflect it on the payment (also streams to OpenSearch).
      if (money(alreadyRefunded + amount) >= money(Number(tx.amount))) {
        await db.query(`UPDATE transactions SET status = 'refunded' WHERE transaction_id = $1`, [transactionId]);
      }
      return json(201, { refund: mapRefund(refundRow) });
    }

    // ---------------------------------------------------------------- disputes
    // GET /disputes/{id}
    if (method === "GET" && pathParams.id && rawPath.includes("/disputes/")) {
      const scope = tenantScopeAt(1);
      const params = [...scope.params, pathParams.id];
      const r = await db.query(
        `SELECT * FROM disputes WHERE ${scope.clause} AND dispute_id = $${params.length}`,
        params
      );
      if (r.rows.length === 0) return json(404, { message: "Dispute not found" });
      return json(200, { dispute: mapDispute(r.rows[0]) });
    }

    // GET /disputes
    if (method === "GET" && rawPath.endsWith("/disputes")) {
      const scope = tenantScopeAt(1);
      let clause = scope.clause;
      const params = [...scope.params];
      if (qs.status) {
        params.push(qs.status);
        clause += ` AND status = $${params.length}`;
      }
      if (qs.transaction_id) {
        params.push(qs.transaction_id);
        clause += ` AND transaction_id = $${params.length}`;
      }
      const r = await db.query(
        `SELECT * FROM disputes WHERE ${clause} ORDER BY created_at DESC LIMIT 200`,
        params
      );
      return json(200, { disputes: r.rows.map(mapDispute) });
    }

    // POST /disputes — open a dispute (demo) and auto-file a Connect Case.
    if (method === "POST" && rawPath.endsWith("/disputes")) {
      const transactionId = String(body.transactionId || "");
      if (!transactionId) return json(400, { message: "transactionId is required" });
      const reason = String(body.reason || "fraudulent");

      const txScope = isAdmin ? { clause: "TRUE", params: [] as string[] } : { clause: "merchant_id = $2", params: [merchantId] };
      const txRes = await db.query(
        `SELECT * FROM transactions WHERE transaction_id = $1 AND ${txScope.clause}`,
        [transactionId, ...txScope.params]
      );
      if (txRes.rows.length === 0) return json(404, { message: "Transaction not found" });
      const tx = txRes.rows[0];

      const amount = body.amount === undefined || body.amount === null ? money(Number(tx.amount)) : money(body.amount);
      const dueBy = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString();
      const id = "dp_" + randomBytes(10).toString("hex");
      const ins = await db.query(
        `INSERT INTO disputes
           (dispute_id, transaction_id, merchant_id, reason, status, amount, currency, evidence, due_by)
         VALUES ($1,$2,$3,$4,'needs_response',$5,$6,$7,$8)
         RETURNING *`,
        [id, transactionId, tx.merchant_id, reason, amount, tx.currency, body.evidence ? String(body.evidence).slice(0, 2000) : null, dueBy]
      );
      const disputeRow = ins.rows[0];

      // Auto-file a tenant-tagged Amazon Connect Case. Best-effort: the dispute
      // is already persisted; a Cases failure only means no linked case_id.
      let caseId: string | null = null;
      try {
        caseId = await createDisputeCase({
          disputeId: id,
          transactionId,
          merchantId: tx.merchant_id,
          merchantName: tx.merchant_name,
          reason,
          amount,
          currency: tx.currency,
          dueBy,
        });
        if (caseId) {
          const upd = await db.query(
            `UPDATE disputes SET case_id = $1, updated_at = now() WHERE dispute_id = $2 RETURNING *`,
            [caseId, id]
          );
          return json(201, { dispute: mapDispute(upd.rows[0]) });
        }
      } catch (e) {
        console.error("dispute -> case creation failed (non-fatal)", e);
      }
      return json(201, { dispute: mapDispute(disputeRow), caseLinked: Boolean(caseId) });
    }

    // POST /disputes/{id}/evidence — submit evidence, move to under_review.
    if (method === "POST" && pathParams.id && rawPath.endsWith("/evidence")) {
      const evidence = String(body.evidence || "").slice(0, 2000);
      if (!evidence) return json(400, { message: "evidence is required" });
      // $1 is the evidence value, so the tenant clause starts at $2.
      const scope = tenantScopeAt(2);
      const params = [evidence, ...scope.params, pathParams.id];
      const r = await db.query(
        `UPDATE disputes SET evidence = $1, status = 'under_review', updated_at = now()
           WHERE ${scope.clause} AND dispute_id = $${params.length}
           RETURNING *`,
        params
      );
      if (r.rows.length === 0) return json(404, { message: "Dispute not found" });
      return json(200, { dispute: mapDispute(r.rows[0]) });
    }

    return json(404, { message: "Not found" });
  } catch (err) {
    console.error(err);
    return json(500, { message: err instanceof Error ? err.message : "Internal error" });
  }
};
