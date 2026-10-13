import { loadConnectConfig } from "./config";
import { loadTokens } from "../auth/tokens";

export interface TransactionRecord {
  transactionId: string;
  merchantId: string;
  merchantName?: string;
  customerName?: string;
  amount?: number | string;
  currency?: string;
  status?: string;
  paymentMethod?: string;
  createdAt?: string;
}

async function apiBase(): Promise<string> {
  const cfg = await loadConnectConfig();
  const base = cfg?.searchApiUrl;
  if (!base) throw new Error("Transaction search is not configured");
  return base.replace(/\/+$/, "");
}

async function authHeaders(): Promise<Record<string, string>> {
  const tokens = loadTokens();
  if (!tokens?.idToken) throw new Error("Not authenticated");
  return { authorization: `Bearer ${tokens.idToken}` };
}

async function parseError(res: Response, fallback: string): Promise<string> {
  try {
    const e = await res.json();
    if (e?.message) return e.message as string;
  } catch {
    // ignore
  }
  return `${fallback} (${res.status})`;
}

/**
 * GET the caller's own transactions from OpenSearch (via the Search API).
 * Tenant isolation is enforced server-side from the JWT — the client cannot
 * request another merchant's transactions.
 */
export async function searchTransactions(opts: {
  q?: string;
  status?: string;
  limit?: number;
} = {}): Promise<{ transactions: TransactionRecord[]; total: number; note?: string }> {
  const base = await apiBase();
  const qs = new URLSearchParams();
  if (opts.q) qs.set("q", opts.q);
  if (opts.status) qs.set("status", opts.status);
  qs.set("limit", String(opts.limit ?? 50));
  const res = await fetch(`${base}/transactions?${qs.toString()}`, {
    method: "GET",
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to search transactions"));
  return res.json();
}

/** GET a single transaction by id (tenant-checked server-side). */
export async function getTransaction(id: string): Promise<TransactionRecord> {
  const base = await apiBase();
  const res = await fetch(`${base}/transactions/${encodeURIComponent(id)}`, {
    method: "GET",
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to load transaction"));
  const body = await res.json();
  return body.transaction as TransactionRecord;
}
