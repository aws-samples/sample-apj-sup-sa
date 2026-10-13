import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/layout/DashboardLayout";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { Badge, paymentTone, humanize } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { SearchIcon } from "@/components/ui/icons";
import { useAuth } from "@/auth/AuthProvider";
import {
  searchTransactions,
  getTransaction,
  type TransactionRecord,
} from "@/connect/transactionsApi";
import { cn } from "@/lib/cn";

const STATUSES = ["all", "succeeded", "pending", "in_progress", "failed", "refunded", "authorized"];

function fmtAmount(a?: number | string, ccy?: string) {
  const n = typeof a === "string" ? Number(a) : a;
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${ccy ? " " + ccy : ""}`;
}
function fmtDate(v?: string | number) {
  if (v == null) return "";
  // OpenSearch maps timestamptz to epoch millis (a number); also handle ISO.
  const d = typeof v === "number" || /^\d+$/.test(String(v)) ? new Date(Number(v)) : new Date(String(v));
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString();
}
const tone = (s?: string) => paymentTone((s ?? "").toLowerCase() as never);

export default function MerchantTransactions() {
  const { user } = useAuth();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [rows, setRows] = useState<TransactionRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<TransactionRecord | null>(null);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    setNote(null);
    try {
      const res = await searchTransactions({
        q: q.trim() || undefined,
        status: status === "all" ? undefined : status,
        limit: 50,
      });
      setRows(res.transactions);
      setTotal(res.total);
      setNote(res.note ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [q, status]);

  // Initial load + re-run when the status filter changes.
  useEffect(() => {
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const openDetail = useCallback(async (id: string) => {
    try {
      setSelected(await getTransaction(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load transaction");
    }
  }, []);

  return (
    <>
      <PageHeader
        title="Transactions"
        description={
          user?.merchantName
            ? `Search and check the status of ${user.merchantName}'s transactions. Only your own transactions are shown.`
            : "Search and check the status of your transactions."
        }
      />

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Card>
            <div className="flex flex-col gap-3 border-b border-ink-100 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap gap-1">
                {STATUSES.map((s) => (
                  <button
                    key={s}
                    onClick={() => setStatus(s)}
                    className={cn(
                      "rounded-lg px-3 py-1.5 text-sm font-medium capitalize transition-colors",
                      status === s ? "bg-ink-900 text-white" : "text-ink-500 hover:bg-ink-100"
                    )}
                  >
                    {s.replace(/_/g, " ")}
                  </button>
                ))}
              </div>
              <form
                className="relative sm:w-64"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run();
                }}
              >
                <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Search id, customer, method…"
                  className="h-9 w-full rounded-lg border border-ink-200 bg-white pl-9 pr-3 text-sm placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
                />
              </form>
            </div>

            <div className="flex items-center justify-between px-5 py-2.5 text-xs text-ink-400">
              <span>
                {loading ? "Searching…" : (
                  <>Showing <strong className="text-ink-600">{rows.length}</strong> of {total} transaction(s)</>
                )}
              </span>
              <Button size="sm" variant="secondary" onClick={() => void run()}>
                Refresh
              </Button>
            </div>

            {note && (
              <div className="mx-5 mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                {note}
              </div>
            )}

            <div className="divide-y divide-ink-100">
              {!loading && rows.length === 0 && (
                <div className="px-5 py-10 text-center text-sm text-ink-400">
                  No transactions match your search.
                </div>
              )}
              {rows.map((t) => (
                <button
                  key={t.transactionId}
                  onClick={() => void openDetail(t.transactionId)}
                  className={cn(
                    "flex w-full items-center justify-between gap-3 px-5 py-3 text-left transition-colors hover:bg-ink-50",
                    selected?.transactionId === t.transactionId && "bg-brand-50/60"
                  )}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-ink-900">{fmtAmount(t.amount, t.currency)}</span>
                      {t.status && (
                        <Badge tone={tone(t.status)} dot>
                          {humanize(t.status)}
                        </Badge>
                      )}
                    </div>
                    <div className="truncate text-xs text-ink-400">
                      {t.customerName} · {t.paymentMethod ? humanize(t.paymentMethod) : ""} · {fmtDate(t.createdAt)}
                    </div>
                  </div>
                  <span className="shrink-0 font-mono text-[11px] text-ink-400">{t.transactionId}</span>
                </button>
              ))}
            </div>
          </Card>
        </div>

        <div className="lg:col-span-2">
          <Card>
            <CardHeader title="Transaction details" subtitle={selected?.transactionId} />
            <CardBody>
              {!selected ? (
                <p className="text-center text-sm text-ink-400">
                  Select a transaction to view its status and details.
                </p>
              ) : (
                <dl className="space-y-2 text-sm">
                  <Row label="Status">
                    {selected.status ? (
                      <Badge tone={tone(selected.status)} dot>
                        {humanize(selected.status)}
                      </Badge>
                    ) : "—"}
                  </Row>
                  <Row label="Amount">{fmtAmount(selected.amount, selected.currency)}</Row>
                  <Row label="Method">{selected.paymentMethod ? humanize(selected.paymentMethod) : "—"}</Row>
                  <Row label="Customer">{selected.customerName ?? "—"}</Row>
                  <Row label="Merchant">{selected.merchantName ?? selected.merchantId}</Row>
                  <Row label="Date">{fmtDate(selected.createdAt)}</Row>
                  <Row label="Transaction ID">
                    <span className="font-mono text-xs">{selected.transactionId}</span>
                  </Row>
                </dl>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-ink-50 pb-2">
      <dt className="text-ink-400">{label}</dt>
      <dd className="text-right font-medium text-ink-800">{children}</dd>
    </div>
  );
}
