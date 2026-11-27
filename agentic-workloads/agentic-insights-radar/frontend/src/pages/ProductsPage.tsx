// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ButtonDropdown from '@cloudscape-design/components/button-dropdown';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import FileUpload from '@cloudscape-design/components/file-upload';
import Form from '@cloudscape-design/components/form';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Link from '@cloudscape-design/components/link';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator, { type StatusIndicatorProps } from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import Textarea from '@cloudscape-design/components/textarea';
import React from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import { type ApiClient, ApiError } from '@/api/client';
import type { IngestionStatus, KnowledgeDocument, Passage, Product } from '@/api/types';
import { can, useSession } from '@/auth/session';
import ConfirmDelete from '@/components/ConfirmDelete';
import { Crumbs, LoadError, PageLoading, ReloadFailed, TableLoadError } from '@/components/ErrorState';
import { describeError, useNotify } from '@/components/Notifications';
import UnsavedGuard, { DiscardWarning } from '@/components/UnsavedGuard';
import { type AsyncState, useAsync } from '@/hooks/useAsync';
import { ago, clip, fileSize, plural, when } from '@/lib/format';
import { useLoadingPageTitle, usePageTitle } from '@/lib/pageTitle';
import { isSafeHref } from '@/lib/html';

const STATUS: Record<IngestionStatus, [StatusIndicatorProps.Type, string]> = {
  none: ['stopped', 'No documentation'],
  pending: ['pending', 'Waiting to crawl'],
  indexing: ['in-progress', 'Indexing'],
  ready: ['success', 'Ready'],
  failed: ['error', 'Failed'],
};

export function Ingestion({ status }: { status: IngestionStatus }) {
  const [type, label] = STATUS[status];
  return <StatusIndicator type={type}>{label}</StatusIndicator>;
}

/** Statuses that change on their own: the page re-reads itself while one is showing. */
const BUSY: ReadonlySet<IngestionStatus> = new Set(['pending', 'indexing']);
export const PRODUCT_POLL_MS = 15_000;
/** How many times the page re-reads a product that is still working: about 15 minutes. */
export const PRODUCT_POLL_TRIES = 60;

/** The largest document the API accepts (products/routes): checked before the upload starts. */
export const DOCUMENT_MAX_BYTES = 50 * 1024 * 1024;

const lines = (text: string): string[] => text.split('\n').map((s) => s.trim()).filter(Boolean);

/** A passage's source as a reader would name it: "docs.example.com - data residency". */
export function sourceName(url: string): string {
  try {
    const parsed = new URL(url);
    const last = parsed.pathname.split('/').filter(Boolean).pop() ?? '';
    const page = decodeURIComponent(last).replace(/\.[a-z]{2,5}$/i, '').replace(/[-_]+/g, ' ').trim();
    return page ? `${parsed.hostname} \u2013 ${page}` : parsed.hostname;
  } catch {
    return url;
  }
}

interface Draft {
  name: string;
  summary: string;
  capabilities: string;
  segments: string;
  competitors: string;
  doc_urls: string;
  notes: string;
}

export function toDraft(p?: Product): Draft {
  return {
    name: p?.name ?? '',
    summary: p?.summary ?? '',
    capabilities: (p?.capabilities ?? []).join('\n'),
    segments: (p?.segments ?? []).join('\n'),
    competitors: (p?.competitors ?? []).join('\n'),
    doc_urls: (p?.doc_urls ?? []).join('\n'),
    notes: p?.notes ?? '',
  };
}

export function fromDraft(d: Draft): Partial<Product> {
  return {
    name: d.name.trim(),
    summary: d.summary.trim(),
    capabilities: lines(d.capabilities),
    segments: lines(d.segments),
    competitors: lines(d.competitors),
    doc_urls: lines(d.doc_urls),
    notes: d.notes.trim(),
  };
}

/** Backend limits (product/model.py), checked here so Save does not fail with a server message. */
export const PRODUCT_NAME_MAX = 120;
export const PRODUCT_TEXT_MAX = 4000;
/** The API's item limits (api/routes.py _profile): 40 documentation URLs, 100 of each list (r3 F3-054). */
export const PRODUCT_URLS_MAX = 40;
export const PRODUCT_ITEMS_MAX = 100;

/** A documentation URL without its query and fragment, which can carry an access token (r3 F3-024). */
export function withoutQuery(url: string): string {
  try {
    const u = new URL(url);
    return `${u.host}${u.pathname === '/' ? '' : u.pathname}`;
  } catch {
    return url.replace(/[?#][\s\S]*$/, '');
  }
}

/** What is wrong with a profile draft, field by field (empty when it can be saved). */
export function draftErrors(d: Draft): { name?: string; summary?: string; notes?: string; doc_urls?: string; capabilities?: string; segments?: string; competitors?: string } {
  const bad = lines(d.doc_urls).filter((u) => !/^https:\/\/[^\s/]+\.[^\s]+$/.test(u));
  const over = (text: string) => (text.trim().length > PRODUCT_TEXT_MAX ? `At most ${PRODUCT_TEXT_MAX} characters (this is ${text.trim().length}).` : undefined);
  const many = (text: string, max: number) => (lines(text).length > max ? `At most ${max} lines (this has ${lines(text).length}).` : undefined);
  const urls = many(d.doc_urls, PRODUCT_URLS_MAX);
  return {
    ...(d.name.trim() ? {} : { name: 'Enter the product name.' }),
    ...(d.name.trim().length > PRODUCT_NAME_MAX ? { name: `At most ${PRODUCT_NAME_MAX} characters (this is ${d.name.trim().length}).` } : {}),
    ...(over(d.summary) ? { summary: over(d.summary) } : {}),
    ...(over(d.notes) ? { notes: over(d.notes) } : {}),
    ...(bad.length ? { doc_urls: `Each line must be a full https:// address: ${bad.slice(0, 3).join(', ')}${bad.length > 3 ? ', ...' : ''}` } : {}),
    ...(urls && !bad.length ? { doc_urls: urls } : {}),
    ...(['capabilities', 'segments', 'competitors'] as const).reduce(
      (acc, k) => {
        const e = many(d[k], PRODUCT_ITEMS_MAX);
        return e ? { ...acc, [k]: e } : acc;
      },
      {} as Record<string, string>,
    ),
  };
}

function ProfileForm({ draft, onChange }: { draft: Draft; onChange: (d: Draft) => void }) {
  const errors = draftErrors(draft);
  const field = (key: keyof Draft, label: string, description: string, rows = 0) => (
    <FormField label={label} description={description} errorText={(errors as Record<string, string | undefined>)[key]}>
      {rows ? (
        <Textarea value={draft[key]} rows={rows} onChange={({ detail }) => onChange({ ...draft, [key]: detail.value })} />
      ) : (
        <Input value={draft[key]} onChange={({ detail }) => onChange({ ...draft, [key]: detail.value })} />
      )}
    </FormField>
  );
  return (
    <SpaceBetween size="m">
      {field('name', 'Name', 'The product as your customers know it.')}
      {field('summary', 'Summary', 'What it is and who it is for, in a few sentences.', 3)}
      <ColumnLayout columns={3}>
        {field('capabilities', 'Capabilities', 'One per line.', 6)}
        {field('segments', 'Target segments', 'One per line.', 6)}
        {field('competitors', 'Known competitors', 'One per line.', 6)}
      </ColumnLayout>
      {field('doc_urls', 'Public documentation', 'One https URL per line. Pages under each URL are read weekly.', 3)}
      {field('notes', 'Notes for the modules', 'Positioning, pricing model, what is out of scope.', 3)}
    </SpaceBetween>
  );
}

/** The first problem with a draft, as a reason for a disabled Save or Add. */
const blockedBy = (d: Draft): string => Object.values(draftErrors(d))[0] ?? '';

export function ProductsPage() {
  usePageTitle('Products');
  const { api, me } = useSession();
  const { fail } = useNotify();
  const navigate = useNavigate();
  const products = useAsync(() => api.products(), [api]);
  const manage = can(me, 'product.manage');
  // ?create=1 opens the form straight away (the Getting started checklist links here).
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = React.useState(() => params.get('create') === '1' && manage);
  const [adding, setAdding] = React.useState(false);
  const [draft, setDraft] = React.useState<Draft>(toDraft());
  const [leaving, setLeaving] = React.useState(false);
  const drop = () => {
    setCreating(false);
    setLeaving(false);
    if (params.has('create')) setParams({}, { replace: true });
  };
  // A filled-in profile is never thrown away by Esc or a stray click on the backdrop.
  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft());
  const close = () => (dirty ? setLeaving(true) : drop());
  const blocked = blockedBy(draft);
  return (
    <>
      <Table wrapLines
        loading={products.loading && !products.data}
        items={products.data?.items ?? []}
        trackBy="id"
        header={
          <Header
            variant="h1"
            description="Start here: describe what you sell (capabilities, customers, competitors) and point to its web pages and documents. The Gaps and Content ideas modules compare what the market says with this profile."
            actions={manage && <Button variant="primary" onClick={() => setCreating(true)}>Add product</Button>}
          >
            Products
          </Header>
        }
        columnDefinitions={[
          {
            id: 'name',
            header: 'Name',
            cell: (p) => <Link href={`/products/${p.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/products/${p.id}`); }}>{p.name}</Link>,
          },
          { id: 'summary', header: 'Summary', cell: (p) => (p.summary ? clip(p.summary, 240) : '-') },
          { id: 'docs', header: 'Documentation', cell: (p) => <Ingestion status={p.web_status} /> },
          { id: 'pages', header: 'Pages', cell: (p) => p.web_pages },
          { id: 'updated', header: 'Updated', cell: (p) => <span title={when(p.updated_at)}>{ago(p.updated_at)}</span> },
        ]}
        empty={
          products.error ? (
            // A failed load must never read as "there are none": that invites a duplicate product.
            <Box textAlign="center" padding="l">
              <SpaceBetween size="xs" alignItems="center">
                <StatusIndicator type="error">{`The products could not be loaded: ${products.error.message}`}</StatusIndicator>
                <Button onClick={products.reload}>Retry</Button>
              </SpaceBetween>
            </Box>
          ) : (
          <Box textAlign="center" padding="l">
            <SpaceBetween size="xs" alignItems="center">
              <b>No products yet.</b>
              {manage ? (
                <>
                  <span>Describe your product first: the Gaps and Content ideas modules need it.</span>
                  <Button onClick={() => setCreating(true)}>Add product</Button>
                </>
              ) : (
                <span>An administrator adds them.</span>
              )}
            </SpaceBetween>
          </Box>
          )
        }
      />
      {creating && (
        <Modal
          visible
          size="large"
          onDismiss={close}
          header="Add product"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={close}>Cancel</Button>
                <Button
                  variant="primary"
                  disabled={Boolean(blocked)}
                  disabledReason={blocked}
                  loading={adding}
                  onClick={() => {
                    setAdding(true); // ids come from the server, so a second click would add a second product
                    void api.createProduct(fromDraft(draft)).then(
                      (p) => navigate(`/products/${p.id}`),
                      (err) => {
                        setAdding(false);
                        fail(err);
                      },
                    );
                  }}
                >
                  Add
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {leaving && <DiscardWarning what="this product" onKeep={() => setLeaving(false)} onDiscard={drop} />}
            <ProfileForm draft={draft} onChange={setDraft} />
          </SpaceBetween>
        </Modal>
      )}
    </>
  );
}

/** Upload with the presigned POST: the browser sends the file straight to S3. */
export async function uploadDocument(api: ApiClient, productId: string, file: File, post = fetch): Promise<void> {
  const ticket = await api.presignUpload(productId, file.name, file.size);
  const form = new FormData();
  for (const [k, v] of Object.entries(ticket.upload.fields)) form.append(k, v);
  form.append('file', file);
  const res = await post(ticket.upload.url, { method: 'POST', body: form });
  if (!res.ok) throw new Error(`Upload of ${file.name} failed (${res.status})`);
  await api.completeUpload(productId, ticket.document.id);
}

function Documents({ product, docs }: { product: Product; docs: AsyncState<{ items: KnowledgeDocument[] }> }) {
  const { api, me } = useSession();
  const { fail, notify } = useNotify();
  const [files, setFiles] = React.useState<File[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [deleting, setDeleting] = React.useState<KnowledgeDocument | null>(null);
  const manage = can(me, 'product.manage');
  const tooBig = files.filter((f) => f.size > DOCUMENT_MAX_BYTES);
  /** Which file is going up now, so ten large PDFs are not one spinner for minutes. */
  const [sending, setSending] = React.useState<{ name: string; index: number; total: number } | null>(null);
  const upload = async () => {
    setBusy(true);
    const total = files.length;
    let stored = 0;
    try {
      for (const f of files) {
        setSending({ name: f.name, index: stored + 1, total });
        await uploadDocument(api, product.id, f);
        stored += 1;
        // Drop each stored file from the selection, so a retry after a failure only sends the rest.
        setFiles((xs) => xs.filter((x) => x !== f));
      }
      notify('success', `${plural(total, 'document')} uploaded. Indexing takes a few minutes.`);
    } catch (err) {
      fail(new Error(`${stored} of ${total} uploaded. ${describeError(err)} The files still listed were not uploaded: choose Upload to try them again.`));
    } finally {
      setBusy(false);
      setSending(null);
      docs.reload(); // also after a partial failure: the files stored so far are listed
    }
  };
  return (
    <>
      <Table wrapLines
        loading={docs.loading && !docs.data}
        items={docs.data?.items ?? []}
        trackBy="id"
        variant="container"
        header={
          <Header
            variant="h2"
            description="Internal documents the modules may search: specs, roadmaps, battlecards. PDF, Word, text, Markdown, HTML or CSV up to 50 MB."
            actions={
              manage && (
                <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                  <FileUpload
                    multiple
                    value={files}
                    onChange={({ detail }) => setFiles(detail.value)}
                    accept=".pdf,.docx,.txt,.md,.html,.htm,.csv"
                    fileErrors={files.map((f) => (f.size > DOCUMENT_MAX_BYTES ? `${fileSize(f.size)}: over the 50 MB limit` : null))}
                    i18nStrings={{
                      uploadButtonText: () => 'Choose files', // this upload always takes several files
                      dropzoneText: () => 'Drop files',
                      removeFileAriaLabel: () => 'Remove file',
                      limitShowFewer: 'Show fewer',
                      limitShowMore: 'Show more',
                      errorIconAriaLabel: 'Error',
                    }}
                  />
                  <Button
                    variant="primary"
                    disabled={!files.length || tooBig.length > 0}
                    disabledReason={files.length ? 'Remove the files over 50 MB first.' : 'Choose one or more files first.'}
                    loading={busy}
                    onClick={() => void upload()}
                  >
                    Upload
                  </Button>
                  {sending && (
                    <StatusIndicator type="loading">{`Uploading ${sending.name} (${sending.index} of ${sending.total})`}</StatusIndicator>
                  )}
                </SpaceBetween>
              )
            }
          >
            Documents
          </Header>
        }
        columnDefinitions={[
          { id: 'name', header: 'File', cell: (d) => d.filename },
          { id: 'status', header: 'Status', cell: (d) => <SpaceBetween size="xxxs"><Ingestion status={d.status} />{d.error && <Box fontSize="body-s" color="text-status-error">{d.error}</Box>}</SpaceBetween> },
          { id: 'size', header: 'Size', cell: (d) => fileSize(d.size) },
          { id: 'when', header: 'Uploaded', cell: (d) => <span title={when(d.uploaded_at)}>{ago(d.uploaded_at)}</span> },
          {
            id: 'del',
            header: 'Actions', // a named column header, for screen readers (CTR QA-090)
            cell: (d) =>
              manage && (
                <Button variant="inline-link" ariaLabel={`Delete ${d.filename}`} onClick={() => setDeleting(d)}>
                  Delete
                </Button>
              ),
          },
        ]}
        empty={docs.error ? <TableLoadError what="documents" error={docs.error} onRetry={docs.reload} /> : <Box textAlign="center" padding="m">No documents.</Box>}
      />
      {deleting && (
        <ConfirmDelete
          header="Delete document"
          onDismiss={() => setDeleting(null)}
          onConfirm={() =>
            api.deleteDocument(product.id, deleting.id).then(() => {
              setDeleting(null);
              docs.reload();
            }, fail)
          }
        >
          {`Delete ${deleting.filename}? The modules stop finding it and this cannot be undone.`}
        </ConfirmDelete>
      )}
    </>
  );
}

/** A passage, cut at 700 characters with Show more: the user searched to read it (r3 F3-105). */
function PassageText({ text }: { text: string }) {
  const [all, setAll] = React.useState(false);
  if (text.length <= 700) return <Box>{text}</Box>;
  return (
    <Box>
      {all ? text : `${text.slice(0, 700)}\u2026`}{' '}
      <Button variant="inline-link" onClick={() => setAll((a) => !a)}>{all ? 'Show less' : 'Show more'}</Button>
    </Box>
  );
}

function Ask({ product }: { product: Product }) {
  const { api, config } = useSession();
  const { fail } = useNotify();
  // The API calls ensure_genai() for this search, so it fails with 503 while AI is paused.
  const paused = !config.genai_enabled;
  const [q, setQ] = React.useState('');
  const [answer, setAnswer] = React.useState<Passage[] | null>(null);
  const [busy, setBusy] = React.useState(false);
  const ask = async () => {
    setBusy(true);
    try {
      setAnswer((await api.askProduct(product.id, q.trim())).items);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Container
      header={
        <Header
          variant="h2"
          description={
            paused
              ? 'An administrator has paused AI features, so this search is paused too: it reads the documentation with the same model the modules use.'
              : 'See what the modules find when they look up this product: ask a question your documentation should answer, for example how it handles data residency.'
          }
        >
          Search product knowledge
        </Header>
      }
    >
      <SpaceBetween size="m">
        <form onSubmit={(e) => { e.preventDefault(); void ask(); }}>
          <div className="air-grow-row">
            <div className="air-grow">
              <Input value={q} onChange={({ detail }) => setQ(detail.value)} placeholder="How does it handle data residency?" ariaLabel="Question" disabled={paused} />
            </div>
            <Button
              variant="primary"
              loading={busy}
              disabled={paused || q.trim().length < 2}
              disabledReason={paused ? 'AI features are paused by an administrator.' : 'Type at least 2 characters.'}
            >
              Search
            </Button>
          </div>
        </form>
        {answer?.length === 0 && <Box>No passages found.</Box>}
        {/* Passages come best first; the raw ranking score means nothing to a reader, so it is not shown. */}
        {answer?.map((p, i) => (
          <div key={i} className="air-passage">
            <Box fontSize="body-s" color="text-body-secondary">
              {`${i + 1}. `}
              {/* The host and the last path part: a documentation URL wrapped over three lines above
                  every passage, which is the part the reader is here for. */}
              {p.source.startsWith('http') && isSafeHref(p.source) ? (
                <a href={p.source} target="_blank" rel="noopener noreferrer" title={p.source}>{sourceName(p.source)}</a>
              ) : (
                p.source
              )}
            </Box>
            <PassageText text={p.text} />
          </div>
        ))}
      </SpaceBetween>
    </Container>
  );
}

/** Which modules compare the market against this product, so the user sees why the profile matters. */
function UsedBy({ productId, forDeletion = false, onUsers }: { productId: string; forDeletion?: boolean; onUsers?: (n: number) => void }) {
  const { api } = useSession();
  const navigate = useNavigate();
  const modules = useAsync(() => api.modules(), [api]);
  const count = modules.data?.items.filter((m) => m.product_id === productId).length;
  React.useEffect(() => {
    if (count !== undefined) onUsers?.(count);
  }, [count, onUsers]);
  if (!modules.data) return null;
  const users = modules.data.items.filter((m) => m.product_id === productId);
  // In the delete dialog: a module that silently loses the product it compares against is the whole
  // point of asking, and the page already knows which ones they are.
  if (forDeletion) {
    return (
      <Box fontSize="body-s" color="text-status-warning" margin={{ top: 's' }}>
        {/* The API refuses to delete a product a module uses (r3 F3-051): say so, rather than "would lose it". */}
        {users.length
          ? `It cannot be deleted while modules compare the market against it: ${users.map((m) => m.name).join(', ')}. Pick another product in them first.`
          : 'No module uses it.'}
      </Box>
    );
  }
  return (
    <Box fontSize="body-s" color="text-body-secondary" margin={{ top: 'm' }}>
      {users.length ? 'Used by: ' : 'No module uses this product yet. Open the Gaps or Content ideas module and pick it under Product.'}
      {users.map((m, i) => (
        <span key={m.slug}>
          {i ? ', ' : ''}
          <Link href={`/modules/${m.slug}`} onFollow={(e) => { e.preventDefault(); navigate(`/modules/${m.slug}`); }}>
            {m.name}
          </Link>
        </span>
      ))}
    </Box>
  );
}

export function ProductPage() {
  const { id = '' } = useParams();
  const { api, me, sessionEnded } = useSession();
  const { fail, notify } = useNotify();
  const navigate = useNavigate();
  const product = useAsync(() => api.product(id), [api, id]);
  // Viewers may see the profile but not the documents or their passages (r2 SR2-12: product.read).
  const readDocs = can(me, 'product.read');
  const docs = useAsync(() => (readDocs ? api.documents(id) : Promise.resolve({ items: [] })), [api, id, readDocs]);
  useLoadingPageTitle(!product.data && !product.error, product.data?.name, 'Products');
  /** How many modules use the product, once known: Delete is refused while any do (r3 F3-051). */
  const [usedBy, setUsedBy] = React.useState<number | null>(null);
  const [editing, setEditing] = React.useState<Draft | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [leavingEdit, setLeavingEdit] = React.useState(false);
  const [resyncing, setResyncing] = React.useState(false);
  // Crawls and indexing finish on their own: re-read while something is still in progress.
  // An ended session answers every poll with a 401, which used to re-open the sign-in dialog every 15 s.
  const pending =
    !sessionEnded && ((!!product.data && BUSY.has(product.data.web_status)) || (docs.data?.items ?? []).some((d) => BUSY.has(d.status)));
  const reloadProduct = product.reload;
  const reloadDocs = docs.reload;
  /** The polling has run its course: a crawl stuck overnight made thousands of requests. */
  const [pollExhausted, setPollExhausted] = React.useState(false);
  // Work that starts again (a new upload, a crawl started elsewhere) is watched again.
  const [wasPending, setWasPending] = React.useState(pending);
  if (pending !== wasPending) {
    setWasPending(pending);
    if (pending) setPollExhausted(false);
  }
  React.useEffect(() => {
    if (!pending || pollExhausted) return undefined;
    let tries = 0;
    const t = setInterval(() => {
      // A hidden tab reads nothing: it has nobody watching it.
      if (document.visibilityState !== 'visible') return;
      tries += 1;
      if (tries > PRODUCT_POLL_TRIES) {
        clearInterval(t);
        setPollExhausted(true);
        return;
      }
      reloadProduct();
      reloadDocs();
    }, PRODUCT_POLL_MS);
    return () => clearInterval(t);
  }, [pending, pollExhausted, reloadProduct, reloadDocs]);
  // A failed poll keeps the page and an open Edit dialog: only a first load that failed replaces it (r3 F3-003).
  if (product.error && !product.data) return <LoadError error={product.error} what="Product" back={{ href: '/products', label: 'Back to products' }} onRetry={product.reload} />;
  if (!product.data) return <PageLoading />;
  const p = product.data;
  const manage = can(me, 'product.manage');
  const crawl = p.stats.crawl as { pages?: number; errors?: string[]; stopped?: string | null } | undefined;
  const errors = crawl?.errors ?? [];
  const save = (d: Draft) => {
    setSaving(true);
    void api
      .updateProduct(p.id, fromDraft(d), p.version)
      .then(
        () => {
          setEditing(null);
          notify('success', 'Saved.');
          product.reload();
        },
        (err: unknown) => {
          // Someone else saved first: every retry sent the same old version (r3 F3-013). The product is
          // read again and the dialog keeps the edits, so Save works on the next try.
          if (err instanceof ApiError && err.status === 409) {
            notify('warning', 'Someone else changed this product while you were editing. It was read again and your edits are still in the dialog: Save puts them on top of the other changes.');
            product.reload();
          } else fail(err);
        },
      )
      .finally(() => setSaving(false));
  };
  const blocked = editing ? blockedBy(editing) : '';
  const editDirty = Boolean(editing) && JSON.stringify(editing) !== JSON.stringify(toDraft(product.data));
  const stopEditing = () => {
    setEditing(null);
    setLeavingEdit(false);
  };
  const closeEdit = () => (editDirty ? setLeavingEdit(true) : stopEditing());
  return (
    <ContentLayout
      breadcrumbs={<Crumbs items={[{ text: 'Products', href: '/products' }, { text: p.name, href: `/products/${p.id}` }]} />}
      header={
        <Header
          variant="h1"
          description={p.summary}
          actions={
            manage && (
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  loading={resyncing}
                  disabled={resyncing}
                  onClick={() => {
                    setResyncing(true); // a second click would start a second crawl
                    void api
                      .resyncProduct(p.id)
                      .then(() => {
                        notify('success', 'Re-reading the product web pages. This takes a few minutes; this page updates when it is done.');
                        setPollExhausted(false); // a new crawl: watch it again, even if the last one wore the polling out
                        product.reload();
                      }, fail)
                      .finally(() => setResyncing(false));
                  }}
                >
                  Re-read web pages
                </Button>
                <Button onClick={() => setEditing(toDraft(p))}>Edit</Button>
                {/* Delete sits in a menu, away from Edit, so a mis-click cannot reach it. */}
                <ButtonDropdown
                  variant="icon"
                  ariaLabel="More actions"
                  items={[
                    {
                      id: 'delete',
                      text: 'Delete product',
                      disabled: Boolean(usedBy),
                      disabledReason: 'Modules still compare the market against it: pick another product in them first (listed under the profile).',
                    },
                  ]}
                  onItemClick={() => setDeleting(true)}
                />
              </SpaceBetween>
            )
          }
        >
          {p.name}
        </Header>
      }
    >
      <SpaceBetween size="l">
        {product.error && <ReloadFailed error={product.error} what="product" onRetry={product.reload} />}
        {pollExhausted && (
          // A stuck crawl read "Indexing" for days as if live (r3 F3-052).
          <Alert
            type="info"
            action={
              <Button
                onClick={() => {
                  setPollExhausted(false);
                  product.reload();
                  docs.reload();
                }}
              >
                Refresh
              </Button>
            }
          >
            This page stopped checking for progress after 15 minutes, so the status below may be out of date. Refresh to see the latest.
          </Alert>
        )}
        <UnsavedGuard dirty={editDirty}>
          The product profile has unsaved changes in the Edit dialog. They are lost if you leave the page now.
        </UnsavedGuard>
        <Container header={<Header variant="h2">Profile</Header>}>
          {/* A restricted profile has empty lists because they are withheld, not because there are none:
              "-" under Capabilities read as a product that does nothing. */}
          {p.restricted && (
            <Box color="text-body-secondary" margin={{ bottom: 's' }}>
              Your role sees the name, summary and documentation sites of a product. Its capabilities, segments, competitors and notes
              are for administrators.
            </Box>
          )}
          <ColumnLayout columns={p.restricted ? 1 : 4} variant="text-grid">
            {!p.restricted && (
              <>
                <div><Box variant="awsui-key-label">Capabilities</Box>{p.capabilities.join(', ') || '-'}</div>
                <div><Box variant="awsui-key-label">Segments</Box>{p.segments.join(', ') || '-'}</div>
                <div><Box variant="awsui-key-label">Competitors</Box>{p.competitors.join(', ') || '-'}</div>
              </>
            )}
            <div>
              <Box variant="awsui-key-label">{p.restricted ? 'Documentation sites' : 'Documentation'}</Box>
              <Ingestion status={p.web_status} />
              <Box fontSize="body-s" color="text-body-secondary">
                {p.web_pages} pages{p.web_crawled_at ? ` \u00b7 read ${ago(p.web_crawled_at)}` : ''}
                {crawl?.stopped === 'page_limit' ? ' \u00b7 page limit reached' : ''}
              </Box>
              {/* Crawl errors quote the addresses: only for those who manage the product (r3 F3-024). */}
              {manage && errors.length > 0 && (
                <Box fontSize="body-s" color="text-status-error">
                  {`Last crawl problems: ${errors.slice(0, 3).join('; ')}${errors.length > 3 ? ` (and ${errors.length - 3} more)` : ''}`}
                </Box>
              )}
              {p.doc_urls.length > 0 && (
                <ul className="air-evidence">
                  {p.doc_urls.map((u) =>
                    // A documentation URL can carry an access token in its query: only managers see it whole (r3 F3-024).
                    manage ? (
                      <li key={u}>{isSafeHref(u) ? <Link href={u} external fontSize="body-s">{u}</Link> : u}</li>
                    ) : (
                      <li key={u}><Box fontSize="body-s">{withoutQuery(u)}</Box></li>
                    ),
                  )}
                </ul>
              )}
            </div>
          </ColumnLayout>
          {/* Module links are hidden from those without module.manage, so "No module uses this product"
              was false for them, with an instruction they could not follow (r3 F3-023). */}
          {can(me, 'module.manage') && <UsedBy productId={p.id} onUsers={setUsedBy} />}
          {p.notes && <Box margin={{ top: 'm' }}><div className="air-pre">{p.notes}</div></Box>}
        </Container>
        {readDocs ? (
          <>
            <Ask product={p} />
            <Documents product={p} docs={docs} />
          </>
        ) : (
          <Alert type="info">Editors and administrators can search this product's documents and see the list of documents.</Alert>
        )}
      </SpaceBetween>
      {editing && (
        <Modal
          visible
          size="large"
          onDismiss={closeEdit}
          header={`Edit ${p.name}`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={closeEdit}>Cancel</Button>
                <Button
                  variant="primary"
                  loading={saving}
                  // Save with nothing changed wrote a version and an audit row and said "Saved." (r3 F3-053).
                  disabled={Boolean(blocked) || !editDirty}
                  disabledReason={blocked || 'Nothing changed yet.'}
                  onClick={() => save(editing)}
                >
                  Save
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <Form>
            <SpaceBetween size="m">
              {leavingEdit && <DiscardWarning what="these changes" onKeep={() => setLeavingEdit(false)} onDiscard={stopEditing} />}
              <ProfileForm draft={editing} onChange={setEditing} />
            </SpaceBetween>
          </Form>
        </Modal>
      )}
      {deleting && (
        <ConfirmDelete header="Delete product" onDismiss={() => setDeleting(false)} onConfirm={() => api.deleteProduct(p.id).then(() => navigate('/products'), fail)}>
          {`Delete the product "${p.name}"? This cannot be undone.`}
          <UsedBy productId={p.id} forDeletion />
        </ConfirmDelete>
      )}
    </ContentLayout>
  );
}
