// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import AppLayout from '@cloudscape-design/components/app-layout';
import { I18nProvider } from '@cloudscape-design/components/i18n';
import messages from '@cloudscape-design/components/i18n/messages/all.en';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Modal from '@cloudscape-design/components/modal';
import SideNavigation, { type SideNavigationProps } from '@cloudscape-design/components/side-navigation';
import SpaceBetween from '@cloudscape-design/components/space-between';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import type { Nav } from '@/api/types';
import { can, seesPrivate, useSession } from '@/auth/session';
import { useAnyDirty, useRescues } from '@/lib/dirty';
import { KIND_LABELS, nodeList, PRIVATE_KINDS, roleLabel } from '@/lib/format';
import { pageTitle } from '@/lib/pageTitle';
import { appName, listOrder, listVisible, sectionVisible } from '@/lib/profile';

import ChatPanel from './ChatPanel';
import { ChatConversation } from './conversation';
import { ErrorState, PageLoading } from './ErrorState';
import { NavProvider, useNav } from './NavContext';
import { NotificationBar, NotificationProvider, useNotify } from './Notifications';

/** The deployment's name (a profile may rename it); read at render time. */
export const APP_NAME = (): string => appName();

/** Reloads the page; an object so tests can watch it (jsdom cannot reload). */
export const pageReload = { now: (): void => window.location.reload() };

/** Cloudscape's mobile breakpoint: below it the side navigation is an overlay over the content. */
export const MOBILE_WIDTH = 688;

/** `privateOk` is false for a viewer: the lists written from private product documents are left out. */
/** The tab's sessionStorage, or none where the browser refuses it (some private modes throw on access). */
function sessionStore(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function navItems(nav: Nav | undefined, admin: boolean, privateOk = true): SideNavigationProps.Item[] {
  // Lists a deployment hides are left out; the rest follow the profile's order (r: deployment profile).
  const lists: { kind: string; text: string; at: number }[] = Object.entries(KIND_LABELS)
    .filter(([k]) => (privateOk || !PRIVATE_KINDS.has(k)) && listVisible(k))
    .map(([k, text], i) => ({ kind: k, text, at: listOrder(k) * 1000 + i }));
  (nav?.custom ?? []).forEach((c, i) => {
    if (listVisible(c.kind)) lists.push({ kind: c.kind, text: c.label, at: listOrder(c.kind) * 1000 + 500 + i });
  });
  const insightLinks: SideNavigationProps.Item[] = lists
    .sort((a, b) => a.at - b.at)
    .map(({ kind, text }) => ({ type: 'link', text, href: `/insights/${kind}` }));
  const items: SideNavigationProps.Item[] = [
    { type: 'link', text: 'Overview', href: '/' },
    { type: 'link', text: "What's new", href: '/whats-new' },
    { type: 'section', text: 'Insights', items: insightLinks },
    ...(sectionVisible('radars') ? [{ type: 'link', text: 'Radars', href: '/radars' } as const] : []),
    { type: 'link', text: 'Knowledge graph', href: '/graph' },
    ...(sectionVisible('products') ? [{ type: 'link', text: 'Products', href: '/products' } as const] : []),
    { type: 'link', text: 'Modules', href: '/modules' },
  ];
  if (admin) items.push({ type: 'divider' }, { type: 'link', text: 'Administration', href: '/admin' });
  return items;
}

/** The side-navigation item a page belongs to: detail pages highlight their list. */
export function activeNavHref(pathname: string): string {
  const parts = pathname.split('/').filter(Boolean);
  if (parts[0] === 'insights' && parts[1] === 'nodes') return nodeList(decodeURIComponent(parts[2] ?? ''))?.href ?? pathname;
  if (parts[0] === 'insights') return `/insights/${parts[1] ?? ''}`;
  return parts.length ? `/${parts[0]}` : '/';
}

function Shell({ children }: { children: React.ReactNode }) {
  const { me, meLoading, meError, refreshMe, config, signOut, sessionEnded, signedOut, signInAgain } = useSession();
  const { nav } = useNav();
  const { notify, clearLinks } = useNotify();
  /** The long banners can be put away: on a 390 px phone they took a third of the screen (r3 F3-030). */
  const [pausedSeen, setPausedSeen] = React.useState(false);
  /** The version this tab started with: a deploy while it is open is said, with Reload (r3 F3-099). */
  const [loadedVersion] = React.useState(config.version);
  const [profileSeen, setProfileSeen] = React.useState<Error | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  // On a phone the open navigation covers the page, so it starts closed there.
  const [navOpen, setNavOpen] = React.useState(() => window.innerWidth >= MOBILE_WIDTH);
  const [chatOpen, setChatOpen] = React.useState(false);
  const [signingOut, setSigningOut] = React.useState(false);
  const [confirmSignOut, setConfirmSignOut] = React.useState(false);
  // Sign out leaves the app at once, taking any page with unsaved changes with it.
  const anyDirty = useAnyDirty();
  // The pages' own ways to take unsaved work along (a CSV of entry changes): offered wherever the
  // app has to be left, not only in the session-expired dialog (r3 F3-004, F3-041).
  const rescues = useRescues();
  const rescueButtons = rescues.map((r) => (
    <Button key={r.label} iconName="download" onClick={r.run}>{r.label}</Button>
  ));
  // Held here because closing the drawer unmounts the panel; the conversation must survive that.
  const [conversation] = React.useState(() => new ChatConversation(sessionStore()));
  const subject = me?.subject ?? '';
  React.useEffect(() => conversation.claim(subject), [conversation, subject]);
  // A route change is announced and the page's heading takes focus, so a screen-reader or keyboard
  // user is not left on the link they clicked with nothing said.
  // A page that loads its name first (a radar, a module, a product, an insight) is announced and
  // focused once it has it: before, focus stayed on the link (the page was a spinner) and the live
  // region read "Radars" and then "Rivals - Radars".
  const title = React.useSyncExternalStore(pageTitle.subscribe, pageTitle.state);
  const firstPage = React.useRef(true);
  const wantFocus = React.useRef(false);
  React.useEffect(() => {
    if (firstPage.current) firstPage.current = false; // the first page was not navigated to: focus stays where it is
    else wantFocus.current = true;
  }, [location.pathname]);
  // On a phone the assistant covers the page: a cited in-app link navigated underneath it and the tap
  // seemed to do nothing (r3 F3-071). The drawer closes, and the conversation is kept.
  const [path, setPath] = React.useState(location.pathname);
  if (path !== location.pathname) {
    setPath(location.pathname);
    if (window.innerWidth < MOBILE_WIDTH) setChatOpen(false);
  }
  // A toast with a link is about the page it was raised on: it goes when the page changes.
  React.useEffect(() => clearLinks(), [location.pathname, clearLinks]);
  React.useEffect(() => {
    // The store, not `title`: the new page names itself in its own effect, which runs before this one.
    if (!wantFocus.current || !pageTitle.state().ready) return;
    wantFocus.current = false;
    const heading = document.querySelector('#air-page h1');
    if (!(heading instanceof HTMLElement)) return;
    heading.tabIndex = -1;
    heading.focus();
  }, [location.pathname, title]);
  // An administrator can change a role while the app is open: say so, rather than letting every
  // action fail with "forbidden" until the page is reloaded.
  const role = me?.role;
  const known = React.useRef(role);
  React.useEffect(() => {
    if (role && known.current && role !== known.current) {
      notify('info', `Your role is now ${roleLabel(role)}. What the app offers you has changed with it.`);
    }
    if (role) known.current = role;
  }, [role, notify]);
  // Every navigation item is an in-app link.
  const follow = (e: CustomEvent<{ href: string }>) => {
    e.preventDefault();
    navigate(e.detail.href);
  };
  const leave = () => {
    setConfirmSignOut(false);
    setSigningOut(true);
    void signOut();
  };
  const askToLeave = () => (anyDirty ? setConfirmSignOut(true) : leave());
  return (
    <>
      <div id="air-top">
        <TopNavigation
          identity={{
            href: '/',
            title: appName(),
            onFollow: (e) => {
              e.preventDefault();
              navigate('/');
            },
          }}
          utilities={[
            {
              type: 'button',
              text: 'Ask',
              iconName: 'gen-ai',
              // The button toggles the drawer, so its name has to follow what it will do. "Hide", not
              // "Close": the drawer's own close button already owns that name.
              ariaLabel: chatOpen ? 'Hide the assistant' : 'Open the assistant',
              disableUtilityCollapse: true,
              onClick: () => setChatOpen((o) => !o),
            },
            {
              type: 'menu-dropdown',
              text: signingOut ? 'Signing out\u2026' : (me?.email ?? (meLoading ? '\u2026' : 'Account')),
              description: me ? `Role: ${roleLabel(me.role)}` : undefined,
              iconName: 'user-profile',
              disableUtilityCollapse: true,
              // Revoking the token can take a few seconds: the item is disabled meanwhile, so one sign-out is enough.
              // The version is here so "which version are you on?" has an answer without asking support.
              items: [
                { id: 'version', text: `Version ${config.version}`, disabled: true },
                { id: 'signout', text: signingOut ? 'Signing out\u2026' : 'Sign out', disabled: signingOut },
              ],
              onItemClick: ({ detail }) => detail.id === 'signout' && askToLeave(),
            },
          ]}
        />
      </div>
      <AppLayout
        headerSelector="#air-top"
        navigationOpen={navOpen}
        onNavigationChange={({ detail }) => setNavOpen(detail.open)}
        navigation={
          <SideNavigation
            activeHref={activeNavHref(location.pathname)}
            header={{ href: '/', text: appName() }}
            items={navItems(nav, can(me, 'user.manage'), seesPrivate(me))}
            onFollow={follow}
          />
        }
        toolsHide={!chatOpen}
        toolsOpen={chatOpen}
        onToolsChange={({ detail }) => setChatOpen(detail.open)}
        tools={<ChatPanel conversation={conversation} />}
        toolsWidth={460}
        ariaLabels={{ tools: 'Assistant', toolsClose: 'Close the assistant', navigation: 'Navigation' }}
        stickyNotifications
        notifications={
          <SpaceBetween size="xs">
            {!config.genai_enabled && !pausedSeen && (
              <Alert type="warning" header="AI features are paused" dismissible dismissAriaLabel="Hide this message" onDismiss={() => setPausedSeen(true)}>
                {`An administrator has paused the assistant, the modules and Search product knowledge. ${sectionVisible('radars') ? 'Radars, the' : 'The'} knowledge graph, ${sectionVisible('radars') ? 'their' : 'its'} searches and every export still work.`}
              </Alert>
            )}
            {config.version !== loadedVersion && (
              <Alert
                type="info"
                header="A new version of the app is available"
                action={<Button onClick={() => pageReload.now()}>Reload</Button>}
              >
                Reload to use it, after saving any changes: reloading leaves this page. This tab still runs the old version, which may not
                work with everything the new one changed.
              </Alert>
            )}
            {signedOut && (
              <Alert
                type="error"
                header={signedOut === 'expired' ? 'Your session has expired' : 'Your account is disabled'}
                action={
                  <SpaceBetween direction="horizontal" size="xs">
                    {rescueButtons}
                    {signedOut === 'expired' ? (
                      <Button onClick={signInAgain}>{anyDirty ? 'Sign in again and lose changes' : 'Sign in again'}</Button>
                    ) : (
                      <Button onClick={askToLeave}>Sign out</Button>
                    )}
                  </SpaceBetween>
                }
              >
                {signedOut === 'expired'
                  ? 'Nothing can be saved until you sign in again, and signing in leaves this page. Take your unsaved changes along first.'
                  : 'Nothing can be saved any more. Take your unsaved changes along first, then sign out.'}
              </Alert>
            )}
            {/* Not while the session is over: "tries again shortly" next to the expired-session dialog
                promised a retry that could not work (r3 F3-045). */}
            {meError && me && !sessionEnded && profileSeen !== meError && (
              <Alert
                type="warning"
                dismissible
                dismissAriaLabel="Hide this message"
                onDismiss={() => setProfileSeen(meError)}
                header="Your profile could not be read again"
                action={<Button loading={meLoading} onClick={refreshMe}>Try again</Button>}
              >
                {`The app keeps working with the role and permissions it last read, and tries again shortly. Unsaved changes on this page are kept. ${meError.message}`}
              </Alert>
            )}
            <NotificationBar />
          </SpaceBetween>
        }
        // Role-dependent parts of every page wait for the profile, so they do not pop in a moment later.
        // Without a profile every can() is false, so an administrator would be shown a viewer's app and
        // told they are not an administrator: say what happened instead, with a way to try again. That
        // is only for a profile never read: a failed background re-read keeps the last one (and the page).
        content={
          <div id="air-page">
            <div className="air-sr-only" aria-live="polite">{title.ready ? title.title : ''}</div>
            {meError && !me ? (
            <ErrorState title="Your profile could not be loaded" onRetry={refreshMe}>
              {`Your account, role and permissions could not be read, so this page cannot be shown. ${meError.message}`}
            </ErrorState>
            ) : !me && meLoading ? (
              <PageLoading />
            ) : (
              children
            )}
          </div>
        }
      />
      {confirmSignOut && (
      <Modal
        visible
        onDismiss={() => setConfirmSignOut(false)}
        header="Sign out without saving?"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setConfirmSignOut(false)}>Stay signed in</Button>
              {rescueButtons}
              <Button variant="primary" onClick={leave}>Sign out and lose changes</Button>
            </SpaceBetween>
          </Box>
        }
      >
        {`A page has unsaved changes. Signing out leaves the app straight away, so they are lost. Choose Stay signed in to save them first${rescues.length ? ', or download them' : ''}.`}
      </Modal>
      )}
    </>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    // Without this the Pagination arrows, and anything else Cloudscape labels from its own bundle,
    // have no accessible name at all: a screen reader reads the page arrows as "button".
    <I18nProvider locale="en" messages={[messages]}>
      <NotificationProvider inline={false}>
        <NavProvider>
          <Shell>{children}</Shell>
        </NavProvider>
      </NotificationProvider>
    </I18nProvider>
  );
}
