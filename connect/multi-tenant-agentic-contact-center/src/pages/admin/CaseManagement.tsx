import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/layout/DashboardLayout";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { StatCard } from "@/components/ui/StatCard";
import { Badge, priorityTone, ticketTone, humanize } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { CaseIcon, PlusIcon, ClockIcon, LifeBuoyIcon } from "@/components/ui/icons";
import {
  listCases,
  createCase,
  getCase,
  updateCase,
  addComment,
  type CaseSummary,
  type CaseDetail,
  type CaseComment,
} from "@/connect/casesApi";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/cn";

const PRIORITIES = ["Low", "Medium", "High", "Urgent"];
const STATUSES = ["Open", "Pending", "Resolved"];
// How many comments to show initially (newest); "show earlier" reveals more.
const COMMENT_PAGE = 5;
// Parse a comment timestamp to millis for stable chronological sorting.
const tsMs = (v?: string) => {
  const n = v ? Date.parse(v) : NaN;
  return Number.isFinite(n) ? n : 0;
};

function toneForPriority(p: string) {
  return priorityTone(p.toLowerCase());
}
function toneForStatus(s: string) {
  return ticketTone(s.toLowerCase());
}

export default function CaseManagement() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [showCreate, setShowCreate] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listCases();
      setCases(res.cases);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load cases");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openCase = useCallback(async (caseId: string) => {
    setSelectedId(caseId);
    setDetailLoading(true);
    setDetail(null);
    try {
      setDetail(await getCase(caseId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load case");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const open = cases.filter((c) => c.status.toLowerCase() === "open").length;
  const urgent = cases.filter(
    (c) => c.priority.toLowerCase() === "urgent" && c.status.toLowerCase() !== "resolved"
  ).length;

  return (
    <>
      <PageHeader
        title="Cases"
        description="Support cases backed by Amazon Connect Cases. Agents pick a case, update its status, and leave comments."
        actions={
          <Button variant="primary" onClick={() => setShowCreate(true)}>
            <PlusIcon className="h-4 w-4" />
            New case
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Total cases" value={cases.length} icon={<CaseIcon className="h-4 w-4" />} />
        <StatCard label="Open" value={open} icon={<LifeBuoyIcon className="h-4 w-4" />} />
        <StatCard label="Urgent" value={urgent} icon={<ClockIcon className="h-4 w-4" />} />
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Card>
            <CardHeader
              title="Case queue"
              subtitle={loading ? "Loading…" : `${cases.length} case(s)`}
              action={
                <Button size="sm" variant="secondary" onClick={() => void refresh()}>
                  Refresh
                </Button>
              }
            />
            <div className="divide-y divide-ink-100">
              {!loading && cases.length === 0 && (
                <div className="px-5 py-10 text-center text-sm text-ink-400">
                  No cases yet. Create the first one.
                </div>
              )}
              {cases.map((c) => (
                <button
                  key={c.caseId}
                  onClick={() => void openCase(c.caseId)}
                  className={cn(
                    "flex w-full flex-col gap-1 px-5 py-3 text-left transition-colors hover:bg-ink-50",
                    selectedId === c.caseId && "bg-brand-50/60"
                  )}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium text-ink-900">{c.title || "(untitled)"}</span>
                    <span className="shrink-0 text-xs text-ink-400">
                      {c.createdAt ? formatRelative(c.createdAt) : ""}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {c.priority && (
                      <Badge tone={toneForPriority(c.priority)} dot>
                        {humanize(c.priority)}
                      </Badge>
                    )}
                    {c.status && <Badge tone={toneForStatus(c.status)}>{humanize(c.status)}</Badge>}
                    {c.merchant && <span className="text-xs text-ink-500">{c.merchant}</span>}
                  </div>
                </button>
              ))}
            </div>
          </Card>
        </div>

        <div className="lg:col-span-2">
          <CaseDetailPanel
            key={selectedId ?? "none"}
            loading={detailLoading}
            detail={detail}
            onChanged={async () => {
              await refresh();
              if (selectedId) await openCase(selectedId);
            }}
          />
        </div>
      </div>

      {showCreate && (
        <CreateCaseModal
          onClose={() => setShowCreate(false)}
          onCreated={async (id) => {
            setShowCreate(false);
            await refresh();
            await openCase(id);
          }}
        />
      )}
    </>
  );
}

function CaseDetailPanel({
  loading,
  detail,
  onChanged,
}: {
  loading: boolean;
  detail: CaseDetail | null;
  onChanged: () => Promise<void>;
}) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Optimistic comments: Amazon Connect Cases SearchRelatedItems is eventually
  // consistent, so a just-added comment often isn't in the immediate re-fetch.
  // Show it right away, then drop it once the server re-fetch includes it.
  const [pending, setPending] = useState<CaseComment[]>([]);
  const [visibleCount, setVisibleCount] = useState(COMMENT_PAGE);
  const commentsRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const server = detail?.comments ?? [];
    setPending((prev) => prev.filter((p) => !server.some((c) => c.body === p.body)));
    setVisibleCount(COMMENT_PAGE); // collapse to the newest when switching cases
  }, [detail]);

  // Chronological order (latest last), merging server + optimistic comments so
  // the order is stable across refreshes.
  const allComments = detail
    ? [...detail.comments, ...pending].sort((a, b) => tsMs(a.createdAt) - tsMs(b.createdAt))
    : [];
  const shownComments = allComments.slice(Math.max(0, allComments.length - visibleCount));
  const hiddenCount = allComments.length - shownComments.length;
  // Keep the newest comment in view (chat-style): scroll to bottom when a comment
  // is added or the case opens — not when older comments are revealed.
  useEffect(() => {
    const el = commentsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [allComments.length]);

  if (!detail && !loading) {
    return (
      <Card>
        <CardBody className="text-center text-sm text-ink-400">
          Select a case to view details, update its status, and add comments.
        </CardBody>
      </Card>
    );
  }

  async function setStatus(status: string) {
    if (!detail) return;
    setBusy(true);
    setErr(null);
    try {
      await updateCase(detail.caseId, { status });
      await onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  async function submitComment() {
    if (!detail || !comment.trim()) return;
    const text = comment.trim();
    setBusy(true);
    setErr(null);
    try {
      await addComment(detail.caseId, text);
      setComment("");
      // Show it immediately and DON'T re-fetch the whole case (that would flash
      // the panel to a loading state — feels like a page refresh). The pending
      // comment is reconciled against the server list next time the case opens.
      setPending((prev) => [...prev, { body: text, createdAt: new Date().toISOString() }]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to add comment");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={loading ? "Loading…" : detail?.title || "Case"}
        subtitle={detail?.caseId}
      />
      {detail && (
        <CardBody className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {detail.priority && (
              <Badge tone={toneForPriority(detail.priority)} dot>
                {humanize(detail.priority)}
              </Badge>
            )}
            {detail.status && <Badge tone={toneForStatus(detail.status)}>{humanize(detail.status)}</Badge>}
            {detail.merchant && <span className="text-xs text-ink-500">{detail.merchant}</span>}
          </div>

          {detail.summary && <p className="text-sm text-ink-600">{detail.summary}</p>}

          <div>
            <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-ink-400">
              Set status
            </div>
            <div className="flex flex-wrap gap-1.5">
              {STATUSES.map((s) => (
                <button
                  key={s}
                  disabled={busy || detail.status === s}
                  onClick={() => void setStatus(s)}
                  className={cn(
                    "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50",
                    detail.status === s
                      ? "bg-ink-900 text-white"
                      : "border border-ink-200 text-ink-600 hover:bg-ink-100"
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-ink-400">
              Comments
            </div>
            {allComments.length === 0 ? (
              <p className="text-xs text-ink-400">No comments yet.</p>
            ) : (
              <>
                {hiddenCount > 0 && (
                  <button
                    onClick={() => setVisibleCount((v) => v + COMMENT_PAGE)}
                    className="mb-2 text-xs font-medium text-brand-600 hover:text-brand-700"
                  >
                    Show earlier comments ({hiddenCount})
                  </button>
                )}
                <div ref={commentsRef} className="max-h-72 space-y-2 overflow-y-auto pr-1">
                  {shownComments.map((c, i) => (
                    <div key={i} className="rounded-lg bg-ink-50 px-3 py-2">
                      <p className="whitespace-pre-wrap break-words text-sm text-ink-700">{c.body}</p>
                      {c.createdAt && (
                        <p className="mt-1 text-[11px] text-ink-400">{formatRelative(c.createdAt)}</p>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
            <div className="mt-3 space-y-2">
              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                rows={3}
                placeholder="Leave a comment for this case…"
                className="w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
              />
              <div className="flex justify-end">
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy || !comment.trim()}
                  onClick={() => void submitComment()}
                >
                  Add comment
                </Button>
              </div>
            </div>
          </div>

          {err && <p className="text-xs text-red-600">{err}</p>}
        </CardBody>
      )}
    </Card>
  );
}

function CreateCaseModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (caseId: string) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [merchant, setMerchant] = useState("");
  const [priority, setPriority] = useState("Medium");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (!title.trim()) {
      setErr("Title is required");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await createCase({
        title: title.trim(),
        summary: summary.trim(),
        merchant: merchant.trim(),
        priority,
        status: "Open",
      });
      await onCreated(res.caseId);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to create case");
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4">
      <div className="w-full max-w-lg">
        <Card>
          <CardHeader title="New case" subtitle="Create a case in Amazon Connect Cases" />
          <CardBody className="space-y-3">
            <Field label="Title">
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                autoFocus
                className="input"
                placeholder="Short summary of the issue"
              />
            </Field>
            <Field label="Details">
              <textarea
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                rows={3}
                className="input"
                placeholder="What is the customer / merchant reporting?"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Merchant">
                <input
                  value={merchant}
                  onChange={(e) => setMerchant(e.target.value)}
                  className="input"
                  placeholder="Merchant name"
                />
              </Field>
              <Field label="Priority">
                <select
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                  className="input"
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            {err && <p className="text-xs text-red-600">{err}</p>}
          </CardBody>
          <div className="flex justify-end gap-2 border-t border-ink-100 px-5 py-3">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void submit()} disabled={busy}>
              {busy ? "Creating…" : "Create case"}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink-600">{label}</span>
      {children}
    </label>
  );
}
