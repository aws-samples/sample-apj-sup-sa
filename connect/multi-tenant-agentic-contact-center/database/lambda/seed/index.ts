import { randomBytes } from "crypto";
import { SecretsManagerClient, GetSecretValueCommand } from "@aws-sdk/client-secrets-manager";
import { Client } from "pg";

// Runs INSIDE the VPC (isolated subnets). Creates the transactions table and
// seeds SEED_COUNT rows spread across the AnyCompanyPay merchant tenants. Idempotent:
// if the table already has rows, it does nothing.
const sm = new SecretsManagerClient({});

const TENANTS = [
  { id: "mch_luxe", name: "Luxe Living" },
  { id: "mch_nova", name: "NovaMart" },
  { id: "mch_pixel", name: "PixelForge" },
  { id: "mch_terra", name: "TerraFoods" },
  { id: "mch_volt", name: "VoltCharge" },
];
const METHODS = ["card", "gcash", "grabpay", "bank_transfer", "apple_pay"];
// Realistic payment lifecycle mix. Weighted toward "succeeded" but with a
// meaningful spread so every status is well represented (~40 rows/tenant means
// each status shows up multiple times per merchant). Used for fresh inserts AND
// as the deterministic set that redistributes statuses across existing rows.
const STATUS_SET = [
  "succeeded",
  "succeeded",
  "succeeded",
  "pending",
  "in_progress",
  "failed",
  "refunded",
  "authorized",
];
const STATUSES = STATUS_SET;
const CURRENCIES = ["SGD", "USD"];
const FIRST = ["Hana", "Sofia", "Juan", "Mei", "Arjun", "Liam", "Noah", "Aisha", "Chen", "Priya", "Omar", "Yuki"];
const LAST = ["Garcia", "Okafor", "Cruz", "Tan", "Sharma", "Lee", "Nguyen", "Kaur", "Wong", "Reyes", "Khan", "Sato"];

const pick = <T>(a: T[]): T => a[Math.floor(Math.random() * a.length)];

export const handler = async () => {
  const secretArn = process.env.DB_SECRET_ARN!;
  const raw = (await sm.send(new GetSecretValueCommand({ SecretId: secretArn }))).SecretString!;
  const secret = JSON.parse(raw) as { username: string; password: string };

  const client = new Client({
    host: process.env.DB_HOST,
    port: Number(process.env.DB_PORT || 5432),
    database: process.env.DB_NAME,
    user: secret.username,
    password: secret.password,
    ssl: { rejectUnauthorized: false }, // Aurora presents an AWS-managed cert
    connectionTimeoutMillis: 15000,
  });

  await client.connect();
  try {
    await client.query(`
      CREATE TABLE IF NOT EXISTS transactions (
        transaction_id   TEXT PRIMARY KEY,
        merchant_id      TEXT        NOT NULL,
        merchant_name    TEXT        NOT NULL,
        customer_name    TEXT,
        amount           NUMERIC(12,2) NOT NULL,
        currency         TEXT        NOT NULL,
        status           TEXT        NOT NULL,
        payment_method   TEXT        NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
      )
    `);
    await client.query(
      `CREATE INDEX IF NOT EXISTS idx_transactions_merchant ON transactions (merchant_id)`
    );

    const existing = await client.query<{ c: number }>("SELECT count(*)::int AS c FROM transactions");
    const freshSeed = existing.rows[0].c === 0;

    if (freshSeed) {
      const target = Number(process.env.SEED_COUNT || 200);
      const now = Date.now();
      const tuples: string[] = [];
      const params: unknown[] = [];
      let p = 1;
      for (let i = 0; i < target; i++) {
        const tenant = TENANTS[i % TENANTS.length]; // even spread across tenants
        const id = "txn_" + randomBytes(12).toString("hex");
        const amount = (Math.random() * 4950 + 50).toFixed(2);
        const created = new Date(now - Math.floor(Math.random() * 90 * 24 * 3600 * 1000)).toISOString();
        const customer = `${pick(FIRST)} ${pick(LAST)}`;
        tuples.push(`($${p++},$${p++},$${p++},$${p++},$${p++},$${p++},$${p++},$${p++},$${p++})`);
        params.push(id, tenant.id, tenant.name, customer, amount, pick(CURRENCIES), pick(STATUSES), pick(METHODS), created);
      }

      // 200 rows x 9 params = 1800 bind params — well under Postgres' 65535 limit.
      await client.query(
        `INSERT INTO transactions
           (transaction_id, merchant_id, merchant_name, customer_name, amount, currency, status, payment_method, created_at)
         VALUES ${tuples.join(",")}
         ON CONFLICT (transaction_id) DO NOTHING`,
        params
      );
      console.log(`inserted ${target} transactions`);
    } else {
      console.log(`transactions already present (${existing.rows[0].c} rows) — normalizing status spread`);
    }

    // Ensure a rich, realistic status spread across ALL rows regardless of prior
    // state (succeeded / pending / in_progress / failed / refunded / authorized).
    // The assignment is DETERMINISTIC by transaction_id (stable across runs) so
    // re-running is idempotent, and — since the cluster streams via logical
    // replication — these UPDATEs propagate to OpenSearch through the zero-ETL
    // CDC pipeline. STATUS_SET values are fixed literals (safe to inline).
    const arrLiteral = "ARRAY[" + STATUS_SET.map((s) => `'${s}'`).join(",") + "]::text[]";
    const upd = await client.query(
      `UPDATE transactions
         SET status = (${arrLiteral})[1 + (abs(hashtext(transaction_id)) % ${STATUS_SET.length})]`
    );

    const after = await client.query<{ c: number }>("SELECT count(*)::int AS c FROM transactions");
    const perTenant = await client.query(
      "SELECT merchant_id, count(*)::int AS c FROM transactions GROUP BY merchant_id ORDER BY merchant_id"
    );
    const perStatus = await client.query(
      "SELECT status, count(*)::int AS c FROM transactions GROUP BY status ORDER BY status"
    );
    console.log(
      `total ${after.rows[0].c} transactions; statuses updated: ${upd.rowCount}; per status:`,
      perStatus.rows,
      "per tenant:",
      perTenant.rows
    );

    // ----------------------------------------------------------------------
    // Commerce resources: refunds + disputes as first-class tables (their own
    // lifecycle/idempotency), a standard payments resource model.
    // ----------------------------------------------------------------------
    await client.query(`
      CREATE TABLE IF NOT EXISTS refunds (
        refund_id        TEXT PRIMARY KEY,
        transaction_id   TEXT        NOT NULL REFERENCES transactions (transaction_id),
        merchant_id      TEXT        NOT NULL,
        amount           NUMERIC(12,2) NOT NULL,
        currency         TEXT        NOT NULL,
        reason           TEXT,
        status           TEXT        NOT NULL,
        notes            TEXT,
        idempotency_key  TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
      )
    `);
    await client.query(`CREATE INDEX IF NOT EXISTS idx_refunds_merchant ON refunds (merchant_id)`);
    await client.query(`CREATE INDEX IF NOT EXISTS idx_refunds_txn ON refunds (transaction_id)`);
    // Idempotency scope: one refund per (merchant, idempotency_key).
    await client.query(
      `CREATE UNIQUE INDEX IF NOT EXISTS uq_refunds_idem
         ON refunds (merchant_id, idempotency_key)
         WHERE idempotency_key IS NOT NULL`
    );

    await client.query(`
      CREATE TABLE IF NOT EXISTS disputes (
        dispute_id       TEXT PRIMARY KEY,
        transaction_id   TEXT        NOT NULL REFERENCES transactions (transaction_id),
        merchant_id      TEXT        NOT NULL,
        reason           TEXT        NOT NULL,
        status           TEXT        NOT NULL,
        amount           NUMERIC(12,2) NOT NULL,
        currency         TEXT        NOT NULL,
        evidence         TEXT,
        due_by           TIMESTAMPTZ,
        case_id          TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
      )
    `);
    await client.query(`CREATE INDEX IF NOT EXISTS idx_disputes_merchant ON disputes (merchant_id)`);
    await client.query(`CREATE INDEX IF NOT EXISTS idx_disputes_txn ON disputes (transaction_id)`);

    // Seed demo refunds/disputes only when empty (idempotent). Both reference
    // real seeded transactions so FK + amount rules hold for demoing the API.
    const refundCount = (await client.query<{ c: number }>("SELECT count(*)::int AS c FROM refunds")).rows[0].c;
    const disputeCount = (await client.query<{ c: number }>("SELECT count(*)::int AS c FROM disputes")).rows[0].c;
    let seededRefunds = 0;
    let seededDisputes = 0;

    if (refundCount === 0) {
      // ~2 refunds per tenant: pick 'succeeded'/'refunded' payments. Mix of
      // partial (half) and full refunds, mostly succeeded with a couple pending.
      const src = await client.query<{ transaction_id: string; merchant_id: string; amount: string; currency: string }>(
        `SELECT DISTINCT ON (merchant_id) transaction_id, merchant_id, amount, currency
           FROM transactions WHERE status IN ('succeeded','refunded')
           ORDER BY merchant_id, transaction_id`
      );
      const src2 = await client.query<{ transaction_id: string; merchant_id: string; amount: string; currency: string }>(
        `SELECT DISTINCT ON (merchant_id) transaction_id, merchant_id, amount, currency
           FROM transactions WHERE status IN ('succeeded','refunded')
           ORDER BY merchant_id, transaction_id DESC`
      );
      const REFUND_REASONS = ["requested_by_customer", "duplicate", "fraudulent", "other"];
      const rows = [...src.rows, ...src2.rows];
      const tuples: string[] = [];
      const params: unknown[] = [];
      let p = 1;
      rows.forEach((r, i) => {
        const full = i % 3 === 0; // every third is a full refund
        const amount = full ? Number(r.amount).toFixed(2) : (Number(r.amount) / 2).toFixed(2);
        const status = i % 5 === 4 ? "refunding" : "succeeded";
        const id = "ref_" + randomBytes(10).toString("hex");
        tuples.push(`($${p++},$${p++},$${p++},$${p++},$${p++},$${p++},$${p++},$${p++},$${p++})`);
        params.push(
          id,
          r.transaction_id,
          r.merchant_id,
          amount,
          r.currency,
          REFUND_REASONS[i % REFUND_REASONS.length],
          status,
          full ? "Full refund (demo seed)" : "Partial refund (demo seed)",
          "seed_" + id
        );
      });
      if (tuples.length) {
        await client.query(
          `INSERT INTO refunds
             (refund_id, transaction_id, merchant_id, amount, currency, reason, status, notes, idempotency_key)
           VALUES ${tuples.join(",")}
           ON CONFLICT (refund_id) DO NOTHING`,
          params
        );
        seededRefunds = tuples.length;
      }
    }

    if (disputeCount === 0) {
      // ~1-2 disputes per tenant with a spread of statuses/reasons/deadlines.
      // Pick 'succeeded' payments NOT already refunded so the demo shows the
      // no-refund-on-disputed rule against clean payments.
      const src = await client.query<{ transaction_id: string; merchant_id: string; amount: string; currency: string }>(
        `SELECT DISTINCT ON (merchant_id) t.transaction_id, t.merchant_id, t.amount, t.currency
           FROM transactions t
           WHERE t.status = 'succeeded'
             AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.transaction_id = t.transaction_id)
           ORDER BY t.merchant_id, t.transaction_id DESC`
      );
      const DISPUTE_REASONS = [
        "fraudulent",
        "product_not_received",
        "duplicate",
        "subscription_canceled",
        "product_unacceptable",
      ];
      const DISPUTE_STATUS = ["needs_response", "under_review", "won", "lost"];
      const now2 = Date.now();
      const tuples: string[] = [];
      const params: unknown[] = [];
      let p = 1;
      src.rows.forEach((r, i) => {
        const status = DISPUTE_STATUS[i % DISPUTE_STATUS.length];
        // open disputes get a future deadline; resolved ones get a past one.
        const dueDays = status === "needs_response" || status === "under_review" ? 7 + (i % 5) : -(3 + (i % 5));
        const dueBy = new Date(now2 + dueDays * 24 * 3600 * 1000).toISOString();
        const id = "dp_" + randomBytes(10).toString("hex");
        tuples.push(`($${p++},$${p++},$${p++},$${p++},$${p++},$${p++},$${p++},$${p++})`);
        params.push(
          id,
          r.transaction_id,
          r.merchant_id,
          DISPUTE_REASONS[i % DISPUTE_REASONS.length],
          status,
          Number(r.amount).toFixed(2),
          r.currency,
          dueBy
        );
      });
      if (tuples.length) {
        await client.query(
          `INSERT INTO disputes
             (dispute_id, transaction_id, merchant_id, reason, status, amount, currency, due_by)
           VALUES ${tuples.join(",")}
           ON CONFLICT (dispute_id) DO NOTHING`,
          params
        );
        seededDisputes = tuples.length;
      }
    }

    const refundsTotal = (await client.query<{ c: number }>("SELECT count(*)::int AS c FROM refunds")).rows[0].c;
    const disputesTotal = (await client.query<{ c: number }>("SELECT count(*)::int AS c FROM disputes")).rows[0].c;
    console.log(
      `refunds: ${refundsTotal} (seeded ${seededRefunds}); disputes: ${disputesTotal} (seeded ${seededDisputes})`
    );

    return {
      seeded: freshSeed,
      count: after.rows[0].c,
      statusesUpdated: upd.rowCount,
      perStatus: perStatus.rows,
      perTenant: perTenant.rows,
      refunds: refundsTotal,
      disputes: disputesTotal,
      seededRefunds,
      seededDisputes,
    };
  } finally {
    await client.end();
  }
};
