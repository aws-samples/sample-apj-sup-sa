// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** The second round of polish findings: chat, products, modules, graph and the radar view. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { httpFrame } from '@/components/chat';
import ChatPanel from '@/components/ChatPanel';
import { ChatConversation } from '@/components/conversation';
import Markdown from '@/components/Markdown';
import { NotificationProvider, useNotify } from '@/components/Notifications';
import { module as module_, node, product, radarView } from '@/test/data';
import { fakeApi, wrap } from '@/test/session';

import { ModulePage, grantConflict, grantOptions } from './ModulesPage';
import { ProductPage, PRODUCT_POLL_MS, PRODUCT_POLL_TRIES, sourceName } from './ProductsPage';
import GraphPage from './GraphPage';
import InsightsPage from './InsightsPage';
import RadarPage from './RadarPage';

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const radarRoutes = (
  <Routes>
    <Route path="/radars/:slug" element={<RadarPage />} />
    <Route path="/radars" element={<div>list</div>} />
  </Routes>
);
const productRoutes = <Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>;
/** A neighbourhood of brief nodes, as the API sends it. */
const hood = (ids: string[]) => ({ nodes: ids.map((id, i) => node({ id, title: `Node ${i}`, degree: 1 })), edges: [] });
const moduleRoutes = <Routes><Route path="/modules/:slug" element={<ModulePage />} /><Route path="/modules" element={<div>list</div>} /></Routes>;

describe('F2-080: every HTTP failure from the runtime says what to do', () => {
  it('words each status', () => {
    expect(httpFrame(401).error).toBe('auth');
    expect(httpFrame(403)).toMatchObject({ error: 'forbidden' });
    expect(httpFrame(429).message).toContain('Wait a moment');
    expect(httpFrame(503).message).toContain('Try again in a moment');
    expect(httpFrame(400).message).toBe('The assistant refused the question (400).');
  });
});

describe('F2-043: a broken answer does not blank the app', () => {
  it('replaces the panel only, and can be tried again', async () => {
    const { api } = fakeApi({});
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    // A conversation whose snapshot throws while the panel renders it: the only way to make the
    // panel itself fail, which before this went to the app's top-level boundary and blanked the nav.
    let broken = true;
    const conversation = new ChatConversation();
    const snapshot = conversation.snapshot.bind(conversation);
    Object.defineProperty(conversation, 'snapshot', {
      value: () => {
        if (broken) throw new Error('a Markdown edge case');
        return snapshot();
      },
    });
    render(wrap(<ChatPanel conversation={conversation} />, { api }));
    error.mockRestore();
    expect(screen.getByText('The assistant stopped working because of a problem showing its answer.')).toBeInTheDocument();
    broken = false;
    fireEvent.click(screen.getByRole('button', { name: 'Show the answers as plain text' }));
    expect(await screen.findByPlaceholderText('Ask a question')).toBeInTheDocument();
  });
});

describe('F2-083: Markdown links', () => {
  it('turns a bare https URL into a link and keeps balanced parentheses in an address', () => {
    const { container } = render(<Markdown markdown={'See https://example.com/report for more.'} />);
    const link = container.querySelector('a');
    expect(link).toHaveAttribute('href', 'https://example.com/report');
    expect(link).toHaveTextContent('https://example.com/report');
    const wiki = render(<Markdown markdown={'[Rust](https://en.wikipedia.org/wiki/Rust_(programming_language))'} />).container;
    expect(wiki.querySelector('a')).toHaveAttribute('href', 'https://en.wikipedia.org/wiki/Rust_(programming_language)');
    expect(wiki.querySelector('a')).toHaveTextContent('Rust');
  });

  it('does not take the full stop after a sentence into the address', () => {
    const { container } = render(<Markdown markdown={'Read https://example.com/a.'} />);
    expect(container.querySelector('a')).toHaveAttribute('href', 'https://example.com/a');
  });
});

describe('F2-079 and F2-121: toasts', () => {
  function Toasts() {
    const { notify } = useNotify();
    return (
      <>
        <button type="button" onClick={() => notify('success', <span>Imported. A new version was saved.</span>, 'radar-imported')}>
          rich
        </button>
        <button type="button" onClick={() => notify('error', 'It broke')}>
          plain error
        </button>
        <button type="button" onClick={() => notify('warning', <span>No key at all</span>)}>
          keyless
        </button>
      </>
    );
  }

  it('de-duplicates a rich toast by its key and keeps it until dismissed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<NotificationProvider><Toasts /></NotificationProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'plain error' }));
    fireEvent.click(screen.getByRole('button', { name: 'rich' }));
    fireEvent.click(screen.getByRole('button', { name: 'rich' }));
    // A rich toast with no key cannot be compared, so it stacks: that is the honest behaviour.
    fireEvent.click(screen.getByRole('button', { name: 'keyless' }));
    fireEvent.click(screen.getByRole('button', { name: 'keyless' }));
    const items = () => createWrapper(document.body).findFlashbar()!.findItems();
    // One import, however often it is sent; the keyless ones cannot be compared, so they stack.
    await waitFor(() => expect(items().filter((x) => x.getElement().textContent?.includes('Imported'))).toHaveLength(1));
    await vi.advanceTimersByTimeAsync(10_000);
    // The import toast carries the only link to the radar, so it waits to be dismissed, and the
    // error it arrived next to is still there rather than pushed out by repeats.
    expect(items().filter((x) => x.getElement().textContent?.includes('Imported'))).toHaveLength(1);
    expect(items().filter((x) => x.getElement().textContent?.includes('No key at all')).length).toBeGreaterThan(1);
    vi.useRealTimers();
  });
});

describe('F2-079: a plain confirmation still goes away by itself', () => {
  function Plain() {
    const { notify } = useNotify();
    return (
      <button type="button" onClick={() => notify('success', 'Saved.')}>
        save
      </button>
    );
  }

  it('times out after six seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    document.body.classList.add('awsui-motion-disabled'); // no exit animation to wait for
    render(<NotificationProvider><Plain /></NotificationProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'save' }));
    expect(createWrapper(document.body).findFlashbar()!.findItems()).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(6500);
    expect(createWrapper(document.body).findFlashbar()!.findItems()).toHaveLength(0);
    document.body.classList.remove('awsui-motion-disabled');
    vi.useRealTimers();
  });
});

describe('F2-109, F2-110, F2-111, F2-112: the product page', () => {
  it('names a passage source by its host and page', () => {
    expect(sourceName('https://docs.example.com/guides/data-residency.html')).toBe('docs.example.com \u2013 data residency');
    expect(sourceName('https://docs.example.com/')).toBe('docs.example.com');
    expect(sourceName('internal note')).toBe('internal note');
  });

  it('starts one crawl however often Re-read is clicked', async () => {
    const { api, calls, fetcher } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
      'POST /api/products/p1/sync': {},
    });
    render(wrap(productRoutes, { api, path: '/products/p1' }));
    const button = await screen.findByRole('button', { name: 'Re-read web pages' });
    const real = fetcher.getMockImplementation()!;
    let release = () => {};
    const held = new Promise<void>((r) => (release = r));
    fetcher.mockImplementationOnce(async (...args: Parameters<typeof real>) => {
      await held;
      return real(...args);
    });
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    fireEvent.click(button);
    release();
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/sync'))).toHaveLength(1));
  });

  it('stops polling a stuck crawl, and pauses while the tab is hidden', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const stuck = { ...product(), web_status: 'indexing' as const };
    const { api, calls } = fakeApi({
      'GET /api/products/p1': stuck,
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
    });
    render(wrap(productRoutes, { api, path: '/products/p1' }));
    expect(await screen.findByText('Indexing')).toBeInTheDocument();
    const hidden = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const before = calls.length;
    await vi.advanceTimersByTimeAsync(5 * PRODUCT_POLL_MS);
    expect(calls.length).toBe(before); // nobody is watching
    hidden.mockRestore();
    await vi.advanceTimersByTimeAsync((PRODUCT_POLL_TRIES + 2) * PRODUCT_POLL_MS);
    const exhausted = calls.length;
    await vi.advanceTimersByTimeAsync(10 * PRODUCT_POLL_MS);
    expect(calls.length).toBe(exhausted); // it gave up instead of asking for ever
    vi.useRealTimers();
  });

  it('names the modules that would lose the product, and the file going up', async () => {
    const { api } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [module_({ slug: 'gap', name: 'Gaps', product_id: 'p1' })] },
    });
    // Opened before the modules have answered: the dialog then says it cannot be deleted (r3 F3-051).
    let answer: () => void = () => undefined;
    const listed = api.modules();
    vi.spyOn(api, 'modules').mockReturnValue(new Promise((resolve) => (answer = () => resolve(listed))));
    const { container } = render(wrap(productRoutes, { api, path: '/products/p1' }));
    await screen.findByRole('heading', { name: 'DeskPro' });
    const more = createWrapper(container)
      .findAllButtonDropdowns()
      .find((b) => b.findNativeButton().getElement().getAttribute('aria-label') === 'More actions')!;
    more.openDropdown();
    more.findItemById('delete')!.click();
    await act(async () => answer());
    expect(await screen.findByText('It cannot be deleted while modules compare the market against it: Gaps. Pick another product in them first.')).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    await screen.findByText('Used by:', { exact: false });
    more.openDropdown();
    expect(screen.getByRole('menuitem', { name: /Delete product/ })).toHaveAttribute('aria-disabled', 'true'); // refused by the API anyway
  });

  it('says no module uses it when none does', async () => {
    const { api } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
    });
    const { container } = render(wrap(productRoutes, { api, path: '/products/p1' }));
    await screen.findByText(/No module uses this product yet/);
    const more = createWrapper(container)
      .findAllButtonDropdowns()
      .find((b) => b.findNativeButton().getElement().getAttribute('aria-label') === 'More actions')!;
    more.openDropdown();
    more.findItemById('delete')!.click();
    expect(await screen.findByText('No module uses it.')).toBeInTheDocument();
  });
});

describe('F2-113 and F2-114: the module configuration', () => {
  it('refuses product knowledge together with an outbound tool', () => {
    expect(grantConflict(['kg_read', 'product_knowledge'])).toBe('');
    expect(grantConflict(['product_knowledge', 'web_search'])).toContain('cannot be combined');
    expect(grantConflict(['web_search'])).toBe('');
    const withPrivate = grantOptions(['product_knowledge']);
    expect(withPrivate.find((o) => o.value === 'web_search')?.disabled).toBe(true);
    expect(withPrivate.find((o) => o.value === 'kg_read')?.disabled).toBeUndefined();
    const withOutbound = grantOptions(['web_fetch']);
    expect(withOutbound.find((o) => o.value === 'product_knowledge')?.disabled).toBe(true);
    expect(grantOptions(['product_knowledge', 'web_fetch']).find((o) => o.value === 'web_fetch')?.disabled).toBeUndefined();
    // A grant the app has no label for is named by its own code.
    expect(grantConflict(['product_knowledge', 'future_tool', 'mcp'])).toContain('Attached MCP servers');
    // The same outbound list as the API (round 38 code review): code interpreter is outbound, crawling sources
    // is not. Radar suggestions are curator-only, and the Tools field shows only for custom modules.
    expect(grantConflict(['product_knowledge', 'code_interpreter'])).toContain('Code interpreter');
    expect(grantConflict(['product_knowledge', 'sources'])).toBe('');
  });

  it('picks a schedule when Run on schedule is turned on from none', async () => {
    const custom = module_({ slug: 'pw', name: 'Pricing watch', builtin: false, enabled: false, interval_minutes: 0, grants: ['kg_read', 'kg_write_output'] });
    const { api } = fakeApi({
      'GET /api/modules/pw': custom,
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/pw/runs': { items: [] },
    });
    const { container } = render(wrap(moduleRoutes, { api, path: '/modules/pw' }));
    await screen.findByRole('heading', { name: 'Pricing watch' });
    const toggle = createWrapper(container).findAllToggles()[0]!;
    toggle.findNativeInput().click();
    await waitFor(() => expect(screen.getByText('Daily')).toBeInTheDocument());
    expect(screen.queryByText(/never runs by itself/)).toBeNull();
  });

  it('warns when it is on with no schedule', async () => {
    const custom = module_({ slug: 'pw', name: 'Pricing watch', builtin: false, enabled: true, interval_minutes: 0, grants: ['kg_read'] });
    const { api } = fakeApi({
      'GET /api/modules/pw': custom,
      'GET /api/models': { items: [] },
      'GET /api/products': { items: [] },
      'GET /api/modules/pw/runs': { items: [] },
    });
    render(wrap(moduleRoutes, { api, path: '/modules/pw' }));
    expect(await screen.findByText(/never runs by itself/)).toBeInTheDocument();
  });
});

describe('F2-062 and F2-124: the radar view', () => {
  it('says a newer version has been saved, and loads it on request', async () => {
    let version = 2;
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => radarView({ version, radar: { ...radarView().radar, current_version: version } }),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/graph/nodes/organization/1': { id: 'organization/1', title: 'Acme Inc', type: 'organization' },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('region', { name: 'Legend entries' });
    version = 5;
    // A hidden tab asks nothing: there is nobody to tell.
    const hidden = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    window.dispatchEvent(new Event('focus'));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText('Version 5 has been saved since this page was opened')).toBeNull();
    hidden.mockRestore();
    window.dispatchEvent(new Event('focus'));
    expect(await screen.findByText('Version 5 has been saved since this page was opened')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load it' }));
    await waitFor(() => expect(screen.queryByText('Version 5 has been saved since this page was opened')).toBeNull());
  });

  it('says nothing when the check itself fails', async () => {
    let loads = 0;
    const { api } = fakeApi({
      'GET /api/radars/rivals': () => {
        loads += 1;
        if (loads > 1) throw { status: 500, message: 'broken' }; // not a 503: the client retries those
        return radarView();
      },
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/graph/nodes/organization/1': { id: 'organization/1', title: 'Acme Inc', type: 'organization' },
    });
    render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('region', { name: 'Legend entries' });
    window.dispatchEvent(new Event('focus'));
    await waitFor(() => expect(loads).toBeGreaterThan(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/has been saved since this page was opened/)).toBeNull();
    expect(screen.getByRole('region', { name: 'Legend entries' })).toBeInTheDocument();
  });

  it('says a linked record is gone instead of offering to open it', async () => {
    const { api } = fakeApi({
      'GET /api/radars/rivals': radarView(),
      'GET /api/radars/rivals/suggestions': { items: [] },
      'GET /api/graph/nodes/organization/1': () => {
        throw { status: 404, message: 'not found' };
      },
    });
    const { container } = render(wrap(radarRoutes, { api, path: '/radars/rivals' }));
    await screen.findByRole('region', { name: 'Legend entries' });
    fireEvent.click(container.querySelector('g[role="button"]')!);
    expect(await screen.findByText('Linked record not found: it may have been deleted.')).toBeInTheDocument();
  });
});

describe('F2-112 and F2-116: an upload in progress, and the search box', () => {
  it('says which file is going up', async () => {
    const { api, fetcher } = fakeApi({
      'GET /api/products/p1': product(),
      'GET /api/products/p1/documents': { items: [] },
      'GET /api/modules': { items: [] },
      'POST /api/products/p1/documents': { upload: { url: 'https://s3.example.com/upload', fields: {} }, document: { id: 'd1' } },
      'POST /api/products/p1/documents/d1/complete': { id: 'd1' },
    });
    const { container } = render(wrap(productRoutes, { api, path: '/products/p1' }));
    await screen.findByText('Documents');
    const upload = createWrapper(container).findFileUpload()!;
    fireEvent.change(upload.findNativeInput().getElement(), {
      target: { files: [new File(['a'], 'one.pdf'), new File(['b'], 'two.pdf')] },
    });
    const real = fetcher.getMockImplementation()!;
    let release = () => {};
    const held = new Promise<void>((r) => (release = r));
    fetcher.mockImplementation(async (...args: Parameters<typeof real>) => {
      if (String(args[0]).includes('s3.example.com')) {
        await held;
        return new Response('', { status: 204 });
      }
      return real(...args);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByText('Uploading one.pdf (1 of 2)', {}, { timeout: 5000 })).toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.queryByText('Uploading one.pdf (1 of 2)')).toBeNull());
  });

  it('follows the address when the words change on the same list', async () => {
    const { api } = fakeApi({
      'GET /api/nav': { builtin: [], custom: [] },
      'GET /api/insights/signals': { items: [] },
    });
    function Go() {
      const navigate = useNavigate();
      return <button type="button" onClick={() => navigate('/insights/signals?q=pricing')}>a shared link</button>;
    }
    render(
      wrap(
        <>
          <Routes>
            <Route path="/insights/:kind" element={<InsightsPage />} />
          </Routes>
          <Go />
        </>,
        { api, path: '/insights/signals?q=sandboxing' },
      ),
    );
    expect(await screen.findByDisplayValue('sandboxing')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'a shared link' }));
    // The box used to keep the old words while the list followed the new ones.
    expect(await screen.findByDisplayValue('pricing')).toBeInTheDocument();
  });
});

describe('F2-075: the graph stays reachable with Tab', () => {
  it('makes every node a native button, so each one is a tab stop', async () => {
    const { api } = fakeApi({ 'GET /api/graph/explore': hood(['signal/1', 'signal/2', 'signal/3']) });
    render(wrap(<GraphPage />, { api, path: '/graph' }));
    const list = await screen.findByRole('list', { name: 'Nodes in this picture' });
    await waitFor(() => expect(within(list).getAllByRole('button')).toHaveLength(3));
    for (const b of within(list).getAllByRole('button')) {
      expect(b.tagName).toBe('BUTTON');
      expect(b.getAttribute('tabindex')).not.toBe('-1');
    }
  });
});

