// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import ButtonDropdown from '@cloudscape-design/components/button-dropdown';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import CopyToClipboard from '@cloudscape-design/components/copy-to-clipboard';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Link from '@cloudscape-design/components/link';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Textarea from '@cloudscape-design/components/textarea';
import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import type { EvidenceEntry, EvidenceGroup, InsightDetail, Me } from '@/api/types';
import { can, seesPrivate, useSession } from '@/auth/session';
import ConfirmDelete from '@/components/ConfirmDelete';
import { Crumbs, LoadError, PageLoading } from '@/components/ErrorState';
import { useNav } from '@/components/NavContext';
import Markdown from '@/components/Markdown';
import NodeLink, { GraphLink } from '@/components/NodeLink';
import { useNotify } from '@/components/Notifications';
import SafeHtml from '@/components/SafeHtml';
import { OnRadars } from '@/components/RadarEntryFinder';
import { sectionVisible } from '@/lib/profile';
import WatchToggle from '@/components/WatchToggle';
import { useAsync } from '@/hooks/useAsync';
import { actorLabel, ago, datedText, hostOf, nodeHref, nodeList, plural, PRIVATE_TYPES, sentence, typeLabel, when } from '@/lib/format';
import { isSafeHref } from '@/lib/html';
import { useLoadingPageTitle } from '@/lib/pageTitle';
import { download, slugFile } from '@/radar/export';

/** The record types that other records are about: only these can be watched. */
const ENTITY_TYPES = new Set(['organization', 'person', 'product', 'topic', 'segment']);

const GROUPS: [EvidenceGroup, string][] = [
  ['supports', 'Supporting evidence'],
  ['contradicts', 'Counter-evidence'],
  ['about', 'About'],
  ['addresses', 'Responds to'],
  ['addressed_by', 'Responses'],
  ['supported_by', 'Used as evidence by'],
  ['drafts', 'Drafts'],
  ['other', 'Related'],
];

const ATTRIBUTE_ORDER = ['request_title', 'problem', 'impact', 'workaround', 'priority', 'content_type', 'audience'];

export function attributeRank(key: string): number {
  const i = ATTRIBUTE_ORDER.indexOf(key);
  return i === -1 ? ATTRIBUTE_ORDER.length : i;
}

export function attributeLabel(key: string): string {
  return sentence(key);
}

/** An attribute value as text: lists as lists, objects as "key: value" lines, never raw JSON. */
function AttributeValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    return (
      <ul className="air-evidence">
        {value.map((v, i) => <li key={i}>{typeof v === 'string' ? v : JSON.stringify(v)}</li>)}
      </ul>
    );
  }
  if (value && typeof value === 'object') {
    return (
      <>
        {Object.entries(value).map(([k, v]) => (
          <div key={k}>{`${sentence(k)}: ${typeof v === 'string' ? v : JSON.stringify(v)}`}</div>
        ))}
      </>
    );
  }
  return <>{typeof value === 'string' ? datedText(value) : String(value)}</>;
}

/** The improvement request (or any attributes) as Markdown, for pasting into a backlog. */
export function attributesMarkdown(title: string, attrs: [string, unknown][]): string {
  const text = (v: unknown): string =>
    Array.isArray(v) ? v.map((x) => `\n  - ${text(x)}`).join('') : v && typeof v === 'object' ? JSON.stringify(v) : String(v);
  return [`## ${title}`, '', ...attrs.map(([k, v]) => `- **${sentence(k)}**: ${text(v)}`)].join('\n');
}

/** The drafts module is a built-in that ships switched off; the API refuses a draft while it is. */
export const DRAFTS_OFF = 'The drafts module is switched off: an administrator can enable it under Modules.';

/** What this user can do next on this kind of entry, using the buttons' exact labels. */
export function nextSteps(type: string, me: Me | null, genai: boolean, draftsReady = true): string[] {
  const vote = can(me, 'feedback.give') ? ['Vote Useful or Not useful: your votes tune what the modules surface next.'] : [];
  const steps: Record<string, string[]> = {
    signal: [
      ...vote,
      ...(genai ? ['Ask about it with Ask at the top right.'] : []),
      'Choose View in Knowledge Graph to see what it touches.',
    ],
    trend: ['Check the supporting signals below, then choose View in Knowledge Graph.', ...vote],
    gap: ['Choose Copy as Markdown to put the improvement request below into your backlog.', ...vote],
    content_idea: [
      ...(can(me, 'working.mark') ? ['Choose "I\'m working on this" so others know.'] : []),
      ...(can(me, 'content.draft') && genai && draftsReady ? ['Choose Write a draft to have the Content drafts module write a first draft.'] : []),
      ...vote,
    ],
    content_draft: ['Choose Download Markdown, edit the draft, and check every claim marked as unverified before you publish.', ...vote],
  };
  return steps[type] ?? [];
}

function Evidence({ entries }: { entries: EvidenceEntry[] }) {
  return (
    <ul className="air-evidence">
      {entries.map((e) => (
        <li key={`${e.edge}-${e.direction}-${e.node.id}`}>
          <Badge>{typeLabel(e.node.type)}</Badge> <NodeLink node={e.node} />
          {e.node.summary && <Box fontSize="body-s" color="text-body-secondary">{e.node.summary}</Box>}
        </li>
      ))}
    </ul>
  );
}

const CANNOT_VOTE = 'Editors and administrators can vote.';

function FeedbackControl({ id, detail, onChange }: { id: string; detail: InsightDetail; onChange: () => void }) {
  const { api, me } = useSession();
  const { fail } = useNotify();
  const [asking, setAsking] = React.useState(false);
  const [comment, setComment] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const fb = detail.feedback;
  if (!fb) return null;
  const vote = async (v: 'up' | 'down', text = '') => {
    setBusy(true);
    try {
      await api.feedback(id, v, text);
      setAsking(false);
      setComment('');
      onChange();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };
  const withdraw = async () => {
    setBusy(true);
    try {
      await api.withdrawFeedback(id);
      onChange();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };
  // Asking again shows the comment already given, so it can be edited instead of retyped.
  const askWhy = () => {
    setComment(fb.mine?.vote === 'down' ? fb.mine.comment : '');
    setAsking(true);
  };
  const allowed = can(me, 'feedback.give');
  return (
    <SpaceBetween direction="horizontal" size="xs" alignItems="center">
      {/* A click on your own Useful withdraws it, as the pressed look promises; Not useful opens its
          comment to edit. Both say which one is yours (r3 F3-076). */}
      <Button
        iconName="thumbs-up"
        variant={fb.mine?.vote === 'up' ? 'primary' : 'normal'}
        disabled={!allowed || busy}
        disabledReason={allowed ? undefined : CANNOT_VOTE}
        loading={busy && !asking}
        onClick={() => void (fb.mine?.vote === 'up' ? withdraw() : vote('up'))}
      >
        {`Useful (${fb.up}${fb.mine?.vote === 'up' ? ', your vote' : ''})`}
      </Button>
      <Button
        iconName="thumbs-down"
        variant={fb.mine?.vote === 'down' ? 'primary' : 'normal'}
        disabled={!allowed || busy}
        disabledReason={allowed ? undefined : CANNOT_VOTE}
        onClick={askWhy}
      >
        {`Not useful (${fb.down}${fb.mine?.vote === 'down' ? ', your vote' : ''})`}
      </Button>
      {allowed && fb.mine && (
        <Button variant="link" disabled={busy} onClick={() => void withdraw()}>
          Withdraw my vote
        </Button>
      )}
      <Modal
        visible={asking}
        onDismiss={() => setAsking(false)}
        header="What is wrong with this entry?"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAsking(false)}>Cancel</Button>
              <Button variant="primary" loading={busy} onClick={() => void vote('down', comment.trim())}>Send</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Comment (optional)" description="Modules read feedback to improve, so a reason helps: wrong facts, weak sources, duplicate, not relevant.">
          <Textarea value={comment} onChange={({ detail }) => setComment(detail.value)} rows={4} />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
}

/** How long someone has been on it: "for 3 days", "for 2 hours", "since just now". */
export function workingFor(iso: string, now = Date.now()): string {
  const hours = Math.floor((now - new Date(iso).getTime()) / 3_600_000);
  if (Number.isNaN(hours)) return '';
  if (hours >= 48) return `for ${Math.floor(hours / 24)} days`;
  if (hours >= 24) return 'for a day';
  if (hours >= 2) return `for ${hours} hours`;
  return 'since just now';
}

/** The API's limit on the notes for the writer (api/routes.py): r3 F3-091. */
export const BRIEF_MAX = 2000;

/** How often the page looks for a requested draft, and for how long. */
export const DRAFT_POLL_MS = 20_000;
export const DRAFT_POLL_TRIES = 30;

function ContentActions({ id, detail, onChange, draftsReady }: { id: string; detail: InsightDetail; onChange: () => void; draftsReady: boolean }) {
  const { api, me, config, sessionEnded } = useSession();
  const navigate = useNavigate();
  const { fail, notify } = useNotify();
  const [briefOpen, setBriefOpen] = React.useState(false);
  const [brief, setBrief] = React.useState('');
  const [busy, setBusy] = React.useState<'working' | 'draft' | null>(null);
  /** Drafts listed when a draft was requested: the page re-reads itself until one more appears. */
  const [waiting, setWaiting] = React.useState<number | null>(null);
  /** The page gave up looking: the draft run may have failed or stopped on its budget. */
  const [gaveUp, setGaveUp] = React.useState(false);
  const drafts = detail.evidence.drafts.length;
  const arrived = waiting !== null && drafts > waiting;
  React.useEffect(() => {
    // An ended session answers every poll with a 401: stop rather than ask again every 20 seconds.
    if (waiting === null || arrived || sessionEnded) return undefined;
    let tries = 0;
    const t = setInterval(() => {
      // A hidden tab neither reads nor counts towards giving up (r3 F3-077); it reads once on return.
      if (document.visibilityState !== 'visible') return;
      tries += 1;
      if (tries > DRAFT_POLL_TRIES) {
        clearInterval(t);
        setGaveUp(true); // the indicator used to turn for ever
      } else onChange();
    }, DRAFT_POLL_MS);
    const back = () => document.visibilityState === 'visible' && onChange();
    document.addEventListener('visibilitychange', back);
    return () => {
      clearInterval(t);
      document.removeEventListener('visibilitychange', back);
    };
  }, [waiting, arrived, sessionEnded, onChange]);
  if (detail.node.type !== 'content_idea') return null;
  // By account id, which is stable; an email can change.
  const mine = detail.working_on?.some((w) => w.user === me?.subject) ?? false;
  const toggleWorking = () => {
    setBusy('working');
    void api
      .setWorking(id, !mine)
      .then(onChange, fail)
      .finally(() => setBusy(null));
  };
  const startDraft = () => {
    setBusy('draft');
    void api
      .requestDraft(id, brief.trim())
      .then(() => {
        setBriefOpen(false);
        setBrief('');
        setWaiting(drafts);
        setGaveUp(false);
        notify(
          'success',
          <>
            The draft is being written (usually a few minutes). It appears on this page and under{' '}
            <Link
              href="/insights/drafts"
              onFollow={(e) => {
                e.preventDefault();
                navigate('/insights/drafts');
              }}
            >
              Drafts
            </Link>{' '}
            when it is ready.
          </>,
          'draft-requested',
        );
      }, fail)
      .finally(() => setBusy(null));
  };
  const paused = !config.genai_enabled;
  return (
    <SpaceBetween direction="horizontal" size="xs">
      <Button disabled={!can(me, 'working.mark')} disabledReason="Editors and administrators can mark this." loading={busy === 'working'} onClick={toggleWorking}>
        {mine ? 'Stop working on this' : "I'm working on this"}
      </Button>
      <Button
        variant="primary"
        iconName="gen-ai"
        disabled={!can(me, 'content.draft') || paused || !draftsReady}
        disabledReason={
          paused ? 'AI features are paused by an administrator.' : !draftsReady ? DRAFTS_OFF : 'Editors and administrators can request drafts.'
        }
        onClick={() => setBriefOpen(true)}
      >
        Write a draft
      </Button>
      {waiting !== null && !arrived && !gaveUp && <StatusIndicator type="in-progress">Writing a draft</StatusIndicator>}
      {gaveUp && !arrived && (
        <StatusIndicator type="warning">
          {can(me, 'health.read')
            ? 'No draft has arrived. Check the Content drafts module under Modules for a failed run.'
            : 'No draft has arrived. The run may have failed; ask an administrator to check the Content drafts module.'}
        </StatusIndicator>
      )}
      <Modal
        visible={briefOpen}
        onDismiss={() => setBriefOpen(false)}
        header="Write a draft"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setBriefOpen(false)}>Cancel</Button>
              <Button
                variant="primary"
                loading={busy === 'draft'}
                disabled={brief.trim().length > BRIEF_MAX}
                disabledReason={`Shorten the notes to ${BRIEF_MAX} characters.`}
                onClick={startDraft}
              >
                Start
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField
          label="Notes for the writer (optional)"
          description="Audience, angle, length, anything to include or avoid."
          constraintText={`${brief.trim().length} of ${BRIEF_MAX} characters.`}
          errorText={brief.trim().length > BRIEF_MAX ? `At most ${BRIEF_MAX} characters.` : undefined}
        >
          <Textarea value={brief} onChange={({ detail }) => setBrief(detail.value)} rows={5} />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
}

/** One draft under a content idea. Collapsed, it downloads nothing: ExpandableSection mounts its
 *  content even while closed, so eight drafts meant eight Markdown downloads on page load. */
function DraftSection({ id, title, when: updated }: { id: string; title: string; when: string }) {
  const [expanded, setExpanded] = React.useState(false);
  return (
    <ExpandableSection expanded={expanded} onChange={({ detail }) => setExpanded(detail.expanded)} headerText={`${title} \u00b7 ${ago(updated)}`}>
      {expanded && <DraftBody id={id} title={title} />}
    </ExpandableSection>
  );
}

function DraftBody({ id, title }: { id: string; title: string }) {
  const { api } = useSession();
  const md = useAsync(() => api.draftMarkdown(id), [api, id]);
  if (md.error) {
    return (
      <SpaceBetween size="xs">
        <StatusIndicator type="error">{md.error.message}</StatusIndicator>
        <Button onClick={md.reload}>Retry</Button>
      </SpaceBetween>
    );
  }
  const text = md.data;
  if (text === undefined) return <Spinner />;
  return (
    <SpaceBetween size="s">
      <Button iconName="download" onClick={() => download(slugFile(title, 'md', 'draft'), text, 'text/markdown')}>
        Download Markdown
      </Button>
      <Markdown markdown={text} />
    </SpaceBetween>
  );
}

export default function InsightDetailPage() {
  const { type = '', uid = '' } = useParams();
  const id = `${type}/${uid}`;
  const { api, me, config } = useSession();
  const { fail, notify } = useNotify();
  const navigate = useNavigate();
  const [deleting, setDeleting] = React.useState(false);
  const detail = useAsync(() => api.insight(id), [api, id]);
  // Only a content idea offers Write a draft, so only that page pays for the module list. The API
  // refuses a draft while the drafts module is off, unless the caller may run modules anyway.
  const modules = useAsync(() => (type === 'content_idea' ? api.modules() : Promise.resolve(null)), [api, type]);
  const drafts = modules.data?.items.find((m) => m.slug === 'draft');
  // A custom module's insight goes back to its own list, not the Knowledge graph (r3 F3-100).
  const { nav } = useNav();
  const kind = detail.data?.node.insight_kind;
  const custom = kind ? nav?.custom.find((c) => c.kind === `insight:${kind}`) : undefined;
  const list = nodeList(type) ?? (custom ? { href: `/insights/${custom.kind}`, label: custom.label } : null);
  // A viewer is kept from gaps, content ideas and drafts: their lists would only refuse them too.
  const privateOk = seesPrivate(me);
  const hiddenType = !privateOk && PRIVATE_TYPES.has(type);
  const back = hiddenType ? { href: '/', label: 'Overview' } : (list ?? { href: '/graph', label: 'Knowledge graph' });
  useLoadingPageTitle(!detail.data && !detail.error, detail.data?.node.title, back.label);
  const draftsReady = !drafts || drafts.enabled || can(me, 'module.run');
  if (detail.error) {
    return (
      <LoadError
        error={detail.error}
        what="Entry"
        back={{ href: back.href, label: `Back to ${back.label.toLowerCase()}` }}
        onRetry={detail.reload}
        forRole={privateOk ? undefined : hiddenType ? 'hidden' : 'maybe'}
      />
    );
  }
  if (!detail.data) return <PageLoading />;
  const d = detail.data;
  const n = d.node;
  const attrs = Object.entries(n.attributes)
    .filter(([k]) => k !== 'markdown_key')
    .sort(([a], [b]) => attributeRank(a) - attributeRank(b) || a.localeCompare(b));
  const steps = nextSteps(n.type, me, config.genai_enabled, draftsReady);
  const source = n.evidence_url && isSafeHref(n.evidence_url) ? n.evidence_url : null;
  return (
    <ContentLayout
      breadcrumbs={<Crumbs items={[{ text: back.label, href: back.href }, { text: n.title, href: nodeHref(id) }]} />}
      header={
        <Header
          variant="h1"
          description={n.summary}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <GraphLink id={id} />
              {/* The digest follows what is about a record, and only entities have records about them. */}
              {ENTITY_TYPES.has(id.slice(0, id.indexOf('/'))) && <WatchToggle id={id} />}
              {/* The only way to take a wrong, duplicated or prompt-injected record out of the graph,
                  where it would otherwise keep showing on the Overview and in chat answers. */}
              {/* In a menu, away from the other buttons, as on products (r3 F3-102). */}
              {can(me, 'graph.edit') && (
                <ButtonDropdown variant="icon" ariaLabel="More actions" items={[{ id: 'delete', text: 'Delete this record' }]} onItemClick={() => setDeleting(true)} />
              )}
            </SpaceBetween>
          }
        >
          {n.title}
        </Header>
      }
    >
      <SpaceBetween size="l">
        {steps.length > 0 && (
          <Alert type="info" header="What you can do">
            {steps.join(' ')}
          </Alert>
        )}
        {(d.feedback || n.type === 'content_idea') && (
          <Container header={<Header variant="h2">Your feedback and actions</Header>}>
            <SpaceBetween size="s">
              <FeedbackControl id={id} detail={d} onChange={detail.reload} />
              <ContentActions id={id} detail={d} onChange={detail.reload} draftsReady={draftsReady} />
            </SpaceBetween>
          </Container>
        )}
        <Container>
          <ColumnLayout columns={source ? 5 : 4} variant="text-grid">
            <div>
              <Box variant="awsui-key-label">Type</Box>
              <div>{n.insight_kind ? sentence(n.insight_kind) : typeLabel(n.type)}</div>
            </div>
            <div>
              <Box variant="awsui-key-label">Updated</Box>
              <div title={when(n.updated_at)}>{ago(n.updated_at)}</div>
            </div>
            <div>
              <Box variant="awsui-key-label">Confidence</Box>
              <div>{typeof n.confidence === 'number' ? `${Math.round(n.confidence * 100)}%` : '-'}</div>
            </div>
            {source && (
              <div>
                <Box variant="awsui-key-label">Source</Box>
                <a href={source} target="_blank" rel="noopener noreferrer">{hostOf(source)}</a>
              </div>
            )}
            <div>
              <Box variant="awsui-key-label">Created by</Box>
              <div>{actorLabel(n.created_by, n.module, n.id)}</div>
            </div>
          </ColumnLayout>
        </Container>
        {n.description && (
          <Container header={<Header variant="h2">Details</Header>}>
            <div className="air-prose"><SafeHtml html={n.description} /></div>
          </Container>
        )}
        {n.type === 'content_draft' && (
          <Container header={<Header variant="h2">Draft</Header>}>
            <DraftBody id={id} title={n.title} />
          </Container>
        )}
        {attrs.length > 0 && (
          <Container
            header={
              <Header
                variant="h2"
                actions={
                  n.type === 'gap' && (
                    <CopyToClipboard
                      variant="button"
                      textToCopy={attributesMarkdown(n.title, attrs)}
                      copyButtonText="Copy as Markdown"
                      copySuccessText="Copied"
                      copyErrorText="Could not copy"
                    />
                  )
                }
              >
                {n.type === 'gap' ? 'Improvement request' : 'Attributes'}
              </Header>
            }
          >
            <ColumnLayout columns={2} variant="text-grid">
              {attrs.map(([k, v]) => (
                <div key={k}>
                  <Box variant="awsui-key-label">{attributeLabel(k)}</Box>
                  <AttributeValue value={v} />
                </div>
              ))}
            </ColumnLayout>
          </Container>
        )}
        {(d.working_on?.length ?? 0) > 0 && (
          <Container header={<Header variant="h2">Working on this</Header>}>
            {d.working_on?.map((w, i) => (
              // Colleagues come back without an id (user ""), so the key cannot be the user alone.
              <div key={`${i}-${w.user}-${w.since}`}>
                {/* The API sends "a colleague" for someone whose email the reader may not see, and a
                    lower-case fragment at the start of a line reads as a mistake. An email stays as it is. */}
                {w.user === me?.subject ? 'You' : w.email.includes('@') ? w.email : sentence(w.email)}{' '}
                <span title={when(w.since)}>{workingFor(w.since)}</span>
              </div>
            ))}
          </Container>
        )}
        {deleting && (
          <ConfirmDelete
            header="Delete this record"
            onDismiss={() => setDeleting(false)}
            onConfirm={() =>
              api.deleteNode(id).then((res) => {
                // Over 500 links: the API queues it, and it may still show in lists for a few minutes.
                if (res.queued) {
                  notify('info', `"${n.title}" is being deleted with its ${plural(Number(res.edges), 'link')}. It will finish shortly; until then it may still appear in lists and answers.`);
                }
                navigate(back.href);
              }, fail)
            }
          >
            {`Delete "${n.title}" from the knowledge graph? It leaves every list and every answer that cites it. A radar entry linked to it keeps its place on its radar and loses the link. This cannot be undone.`}
          </ConfirmDelete>
        )}
        {GROUPS.filter(([g]) => d.evidence[g].length > 0).map(([g, label]) =>
          g === 'drafts' ? (
            <Container key={g} header={<Header variant="h2" counter={`(${d.evidence.drafts.length})`}>{label}</Header>}>
              {d.evidence.drafts.map((e) => (
                <DraftSection key={e.node.id} id={e.node.id} title={e.node.title} when={e.node.updated_at} />
              ))}
            </Container>
          ) : (
            <Container key={g} header={<Header variant="h2" counter={`(${d.evidence[g].length})`}>{label}</Header>}>
              <Evidence entries={d.evidence[g]} />
            </Container>
          ),
        )}
        {sectionVisible('radars') && <OnRadars nodeId={n.id} />}
      </SpaceBetween>
    </ContentLayout>
  );
}
