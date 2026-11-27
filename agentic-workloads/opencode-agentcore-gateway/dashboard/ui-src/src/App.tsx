// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import AppLayout from '@cloudscape-design/components/app-layout';
import BreadcrumbGroup, { type BreadcrumbGroupProps } from '@cloudscape-design/components/breadcrumb-group';
import Flashbar from '@cloudscape-design/components/flashbar';
import SideNavigation, { type SideNavigationProps } from '@cloudscape-design/components/side-navigation';
import Spinner from '@cloudscape-design/components/spinner';
import Box from '@cloudscape-design/components/box';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import { lazy, Suspense, useState } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { useApi } from './api';
import { AppProvider, useApp } from './context';
import { setDark, storedDark } from './theme';
import type { Me } from './types';
import { ErrorState } from './components/common';

const Overview = lazy(() => import('./pages/Overview'));
const UsageExplorer = lazy(() => import('./pages/UsageExplorer'));
const Users = lazy(() => import('./pages/Users'));
const UserDetail = lazy(() => import('./pages/UserDetail'));
const Groups = lazy(() => import('./pages/Groups'));
const Budgets = lazy(() => import('./pages/Budgets'));
const ModelAccess = lazy(() => import('./pages/ModelAccess'));
const RateLimits = lazy(() => import('./pages/RateLimits'));
const AuditLog = lazy(() => import('./pages/AuditLog'));
const Pricing = lazy(() => import('./pages/Pricing'));

const MOON = (
  <svg viewBox="0 0 16 16" focusable="false" aria-hidden="true">
    <path d="M13.5 10.2A6 6 0 0 1 5.8 2.5a6 6 0 1 0 7.7 7.7Z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
  </svg>
);
const SUN = (
  <svg viewBox="0 0 16 16" focusable="false" aria-hidden="true">
    <circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" strokeWidth="2" />
    <path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1 1M12 12l1 1M3 13l1-1M12 4l1-1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

const TITLES: Record<string, string> = {
  overview: 'Overview',
  usage: 'Usage explorer',
  users: 'Users',
  groups: 'Groups',
  budgets: 'Budgets & limits',
  models: 'Model access',
  ratelimits: 'Rate limits',
  audit: 'Audit log',
  pricing: 'Pricing',
};

const NAV: SideNavigationProps.Item[] = [
  { type: 'section-group', title: 'Monitor', items: [
    { type: 'link', text: 'Overview', href: '#/overview' },
    { type: 'link', text: 'Usage explorer', href: '#/usage' },
  ] },
  { type: 'section-group', title: 'Govern', items: [
    { type: 'link', text: 'Users', href: '#/users' },
    { type: 'link', text: 'Groups', href: '#/groups' },
    { type: 'link', text: 'Budgets & limits', href: '#/budgets' },
    { type: 'link', text: 'Model access', href: '#/models' },
    { type: 'link', text: 'Rate limits', href: '#/ratelimits' },
  ] },
  { type: 'section-group', title: 'Admin', items: [
    { type: 'link', text: 'Audit log', href: '#/audit' },
    { type: 'link', text: 'Pricing', href: '#/pricing' },
  ] },
];

function breadcrumbs(pathname: string): BreadcrumbGroupProps.Item[] {
  const seg = pathname.split('/').filter(Boolean);
  const items: BreadcrumbGroupProps.Item[] = [{ text: 'opencode gateway', href: '#/overview' }];
  if (seg[0] && TITLES[seg[0]]) items.push({ text: TITLES[seg[0]], href: `#/${seg[0]}` });
  if (seg[1]) {
    // A malformed escape (#/users/50%) must not throw while rendering the shell: show the raw segment instead.
    let text = seg[1];
    try {
      text = decodeURIComponent(seg[1]);
    } catch {
      /* keep the raw text */
    }
    items.push({ text, href: `#${pathname}` });
  }
  return items;
}

function Shell() {
  const { me, flashes } = useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [dark, setDarkState] = useState(storedDark());
  const [navOpen, setNavOpen] = useState(true);
  const active = `#/${location.pathname.split('/').filter(Boolean)[0] ?? 'overview'}`;
  const follow = (e: CustomEvent<{ href: string; external?: boolean }>) => {
    if (e.detail.external || !e.detail.href.startsWith('#')) return;
    e.preventDefault();
    navigate(e.detail.href.slice(1));
  };

  return (
    <>
      <div id="top-nav" style={{ position: 'sticky', top: 0, zIndex: 1002 }}>
        <TopNavigation
          identity={{ href: '#/overview', title: 'opencode gateway', onFollow: (e) => { e.preventDefault(); navigate('/overview'); } }}
          utilities={[
            {
              type: 'button',
              iconSvg: dark ? SUN : MOON,
              text: dark ? 'Light mode' : 'Dark mode',
              title: 'Toggle dark mode',
              onClick: () => {
                setDark(!dark);
                setDarkState(!dark);
              },
            },
            ...(me
              ? [
                  {
                    type: 'button' as const,
                    text: me.role === 'admin' ? 'Admin' : 'Viewer (read-only)',
                    iconName: me.role === 'admin' ? ('security' as const) : ('lock-private' as const),
                    title: `Signed in with the ${me.role} role`,
                    disableUtilityCollapse: true,
                  },
                  {
                    type: 'menu-dropdown' as const,
                    text: me.email || me.sub || 'Signed in',
                    description: `Role: ${me.role}`,
                    iconName: 'user-profile' as const,
                    items: [
                      { id: 'groups', text: `Groups: ${me.groups.join(', ') || 'none'}`, disabled: true },
                      // Only the ALB mode has a session to end; with IAM (Function URL, local proxy) there is none.
                      ...(me.sign_out ? [{ id: 'signout', text: 'Sign out', href: '/logout' }] : []),
                    ],
                  },
                ]
              : []),
          ]}
        />
      </div>
      <AppLayout
        headerSelector="#top-nav"
        navigationOpen={navOpen}
        onNavigationChange={({ detail }) => setNavOpen(detail.open)}
        navigation={<SideNavigation activeHref={active} header={{ text: 'opencode gateway', href: '#/overview' }} items={NAV} onFollow={follow} />}
        breadcrumbs={<BreadcrumbGroup items={breadcrumbs(location.pathname)} onFollow={follow} ariaLabel="Breadcrumbs" />}
        notifications={<Flashbar items={flashes} stackItems={flashes.length > 2} />}
        toolsHide
        content={
          <Suspense fallback={<Box textAlign="center" padding="xxl"><Spinner size="large" /></Box>}>
            <Routes>
              <Route path="/" element={<Navigate to="/overview" replace />} />
              <Route path="/overview" element={<Overview />} />
              <Route path="/usage" element={<UsageExplorer />} />
              <Route path="/users" element={<Users />} />
              <Route path="/users/:user" element={<UserDetail />} />
              <Route path="/groups" element={<Groups />} />
              <Route path="/groups/:group" element={<Navigate to="/groups" replace />} />
              <Route path="/budgets" element={<Budgets />} />
              <Route path="/models" element={<ModelAccess />} />
              <Route path="/ratelimits" element={<RateLimits />} />
              <Route path="/audit" element={<AuditLog />} />
              <Route path="/pricing" element={<Pricing />} />
              <Route path="*" element={<Navigate to="/overview" replace />} />
            </Routes>
          </Suspense>
        }
      />
    </>
  );
}

export default function App() {
  const me = useApi<Me>('/api/v2/me');
  if (me.error) {
    return (
      <Box padding="xxl">
        <ErrorState error={me.error} onRetry={me.reload} what="your session" />
      </Box>
    );
  }
  return (
    <AppProvider me={me.data}>
      <Shell />
    </AppProvider>
  );
}
