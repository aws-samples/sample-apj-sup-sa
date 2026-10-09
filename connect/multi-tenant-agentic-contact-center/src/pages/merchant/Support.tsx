import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/layout/DashboardLayout";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { Badge, priorityTone, ticketTone, humanize } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PlusIcon } from "@/components/ui/icons";
import { useAuth } from "@/auth/AuthProvider";
import {
  listCases,
  createCase,
  getCase,
  addComment,
  type CaseSummary,
  type CaseDetail,
  type CaseComment,
} from "@/connect/casesApi";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/cn";
import { loadConnectConfig } from "@/connect/config";
import SupportChat from "./SupportChat";
import ScreenShareModal from "@/components/screenshare/ScreenShareModal";

const PRIORITIES = ["Low", "Medium", "High", "Urgent"];
const COMMENT_PAGE = 5;
const tsMs = (v?: string) => {
  const n = v ? Date.parse(v) : NaN;
  return Number.isFinite(n) ? n : 0;
};

const toneP = (p: string) => priorityTone(p.toLowerCase());
const toneS = (s: string) => ticketTone(s.toLowerCase());

export default function MerchantSupport() {
  const { user } = useAuth();
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [chatEnabled, setChatEnabled] = useState(false);
  // Opt-in screen sharing (connect-screenshare/): shown only when configured.
  const [screenShareEnabled, setScreenShareEnabled] = useState(false);
  const [screenShareCaseId, setScreenShareCaseId] = useState<string | null>(null);
  const [chatCaseId, setChatCaseId] = useState<string | null>(null);

  useEffect(() => {
    void loadConnectConfig().then((cfg) => {
      setChatEnabled(Boolean(cfg?.chatApiUrl));
      setScreenShareEnabled(Boolean(cfg?.screenShareApiUrl));
    });
  }, []);

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

  return (
    <>
      <PageHeader
        title="Support"
        description={
          user?.merchantName
            ? `Raise and track support cases for ${user.merchantName}. Only your team can see these.`
            : "Raise and track support cases for your business."
        }
        actions={
          <Button variant="primary" onClick={() => setShowCreate(true)}>
            <PlusIcon className="h-4 w-4" />
            New case
          </Button>
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
            <CardHeader
              title="Your cases"
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
                  No cases yet. Open one if you hit an issue.
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
                      <Badge tone={toneP(c.priority)} dot>
                        {humanize(c.priority)}
                      </Badge>
                    )}
                    {c.status && <Badge tone={toneS(c.status)}>{humanize(c.status)}</Badge>}
                  </div>
                </button>
              ))}
            </div>
          </Card>
        </div>

        <div className="space-y-4 lg:col-span-2">
          <DetailPanel
            key={selectedId ?? "none"}
            loading={detailLoading}
            detail={detail}
            chatEnabled={chatEnabled}
            onStartChat={(id) => setChatCaseId(id)}
            screenShareEnabled={screenShareEnabled}
            onStartScreenShare={(id) => setScreenShareCaseId(id)}
          />
        </div>
      </div>

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={async (id) => {
            setShowCreate(false);
            await refresh();
            await openCase(id);
          }}
        />
      )}

      {screenShareCaseId && (
        <ScreenShareModal caseId={screenShareCaseId} onClose={() => setScreenShareCaseId(null)} />
      )}

      {chatCaseId && (
        <CaseChatModal
          caseId={chatCaseId}
          onClose={() => setChatCaseId(null)}
          onSaved={async () => {
            if (selectedId === chatCaseId) await openCase(chatCaseId);
          }}
        />
      )}
    </>
  );
}

function DetailPanel({
  loading,
  detail,
  chatEnabled,
  onStartChat,
  screenShareEnabled,
  onStartScreenShare,
}: {
  loading: boolean;
  detail: CaseDetail | null;
  chatEnabled: boolean;
  onStartChat: (caseId: string) => void;
  screenShareEnabled: boolean;
  onStartScreenShare: (caseId: string) => void;
}) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Optimistic comments (Connect Cases related-items are eventually consistent).
  const [pending, setPending] = useState<CaseComment[]>([]);
  const [visibleCount, setVisibleCount] = useState(COMMENT_PAGE);
  const commentsRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const server = detail?.comments ?? [];
    setPending((prev) => prev.filter((p) => !server.some((c) => c.body === p.body)));
    setVisibleCount(COMMENT_PAGE);
  }, [detail]);

  // Chronological (latest last), stable across refreshes.
  const allComments = detail
    ? [...detail.comments, ...pending].sort((a, b) => tsMs(a.createdAt) - tsMs(b.createdAt))
    : [];
  const shownComments = allComments.slice(Math.max(0, allComments.length - visibleCount));
  const hiddenCount = allComments.length - shownComments.length;
  useEffect(() => {
    const el = commentsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [allComments.length]);

  if (!detail && !loading) {
    return (
      <Card>
        <CardBody className="text-center text-sm text-ink-400">
          Select a case to view its status and add comments.
        </CardBody>
      </Card>
    );
  }

  async function submitComment() {
    if (!detail || !comment.trim()) return;
    const text = comment.trim();
    setBusy(true);
    setErr(null);
    try {
      await addComment(detail.caseId, text);
      setComment("");
      // Show it immediately without re-fetching the case (a re-fetch flashes the
      // panel to a loading state). Reconciled against the server list on re-open.
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
        action={
          detail && (chatEnabled || screenShareEnabled) ? (
            <div className="flex gap-2">
              {chatEnabled && (
                <Button size="sm" variant="secondary" onClick={() => onStartChat(detail.caseId)}>
                  Chat about this case
                </Button>
              )}
              {screenShareEnabled && (
                <Button size="sm" variant="secondary" onClick={() => onStartScreenShare(detail.caseId)}>
                  Share screen
                </Button>
              )}
            </div>
          ) : undefined
        }
      />
      {detail && (
        <CardBody className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {detail.priority && (
              <Badge tone={toneP(detail.priority)} dot>
                {humanize(detail.priority)}
              </Badge>
            )}
            {detail.status && <Badge tone={toneS(detail.status)}>{humanize(detail.status)}</Badge>}
          </div>

          {detail.summary && <p className="text-sm text-ink-600">{detail.summary}</p>}

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
                placeholder="Add a comment for the support team…"
                className="input"
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

function CreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (caseId: string) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
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
      // No merchant field: the server tags the case with this user's tenant
      // (custom:merchant_id) from the JWT — a merchant can't create for others.
      const res = await createCase({
        title: title.trim(),
        summary: summary.trim(),
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
          <CardHeader title="New support case" subtitle="Tell us what's going wrong" />
          <CardBody className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-ink-600">Title</span>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                autoFocus
                className="input"
                placeholder="Short summary of the issue"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-ink-600">Details</span>
              <textarea
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                rows={4}
                className="input"
                placeholder="What happened? Include any error messages, timestamps, or payment IDs."
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-ink-600">Priority</span>
              <select value={priority} onChange={(e) => setPriority(e.target.value)} className="input">
                {PRIORITIES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
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

function CaseChatModal({
  caseId,
  onClose,
  onSaved,
}: {
  caseId: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4">
      <div className="w-full max-w-lg">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-medium text-white/90">Case chat · {caseId}</span>
          <Button variant="ghost" size="sm" className="text-white" onClick={onClose}>
            Close
          </Button>
        </div>
        {/* Case-bound chat: uses the case chat flow and saves the transcript to
            this case when it ends. */}
        <SupportChat caseId={caseId} onTranscriptSaved={() => void onSaved()} />
      </div>
    </div>
  );
}
