// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import React from 'react';
import { useAuth } from 'react-oidc-context';
import { createBrowserRouter, Navigate, Route, RouterProvider, Routes, useLocation, useNavigate } from 'react-router-dom';

import { CALLBACK_PATH } from '@/auth/session';
import AppShell, { APP_NAME } from '@/components/AppShell';
import { NotFound, PageLoading, RequireCapability, SectionOff } from '@/components/ErrorState';
import { sectionVisible, tagline } from '@/lib/profile';
import { usePageTitle } from '@/lib/pageTitle';
import AdminPage from '@/pages/AdminPage';
import GraphPage from '@/pages/GraphPage';
import HomePage from '@/pages/HomePage';
import InsightDetailPage from '@/pages/InsightDetailPage';
import WhatsNewPage from '@/pages/WhatsNewPage';
import InsightsPage from '@/pages/InsightsPage';
import { ModulePage, ModulesPage } from '@/pages/ModulesPage';
import { ProductPage, ProductsPage } from '@/pages/ProductsPage';
import RadarPage from '@/pages/RadarPage';
import RadarsPage from '@/pages/RadarsPage';
import UnsubscribePage from '@/pages/UnsubscribePage';

/** Goes to the Overview inside the app: a full page load would lose the assistant's conversation. */
function ToOverview() {
  const navigate = useNavigate();
  return <Button onClick={() => navigate('/')}>Go to Overview</Button>;
}

/** Names the crash page, so the tab does not keep the title of the page that crashed (r3 F3-046). */
function CrashTitle() {
  usePageTitle('Something went wrong');
  return null;
}

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  override state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  override render() {
    if (this.state.error) {
      return (
        <Box padding="l">
          <CrashTitle />
          <SpaceBetween size="m">
            <Header variant="h1">Something went wrong on this page</Header>
            <Box>The page stopped working because of a problem in the app. Reload it, or go back to the Overview.</Box>
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="primary" onClick={() => window.location.reload()}>Reload</Button>
              <ToOverview />
            </SpaceBetween>
            <ExpandableSection headerText="Technical details">
              <Box variant="code">{this.state.error.message}</Box>
            </ExpandableSection>
          </SpaceBetween>
        </Box>
      );
    }
    return this.props.children;
  }
}

/** The callback page with or without its query or hash: never a page to return to (its code is spent). */
function isCallback(path: string): boolean {
  return path === CALLBACK_PATH || /^\/auth\/callback[?#/]/.test(path);
}

/** After the OIDC redirect: go to the page the user was on when they chose Sign in. */
export function AfterSignIn() {
  const auth = useAuth();
  const state = auth.user?.state;
  const target = typeof state === 'string' && state.startsWith('/') && !state.startsWith('//') ? state : '/';
  return <Navigate to={isCallback(target) ? '/' : target} replace />;
}

/** Exposed for tests. */
export const ErrorBoundaryForTest = ErrorBoundary;

export function AppRoutes() {
  // Keyed on the path so a crash on one page clears when the user navigates to another.
  const { pathname } = useLocation();
  return (
    <AppShell>
      <ErrorBoundary key={pathname}>
        <Routes>
          <Route path={CALLBACK_PATH} element={<AfterSignIn />} />
          <Route path="/" element={<HomePage />} />
          <Route path="/whats-new" element={<WhatsNewPage />} />
          <Route path="/insights/nodes/:type/:uid" element={<InsightDetailPage />} />
          {/* "Insights" is a section in the side navigation: trimming the address to it is not a 404. */}
          <Route path="/insights" element={<Navigate to="/insights/signals" replace />} />
          <Route path="/insights/:kind" element={<InsightsPage />} />
          <Route path="/radars" element={sectionVisible('radars') ? <RadarsPage /> : <SectionOff what="Radars" />} />
          <Route path="/radars/:slug" element={sectionVisible('radars') ? <RadarPage /> : <SectionOff what="Radars" />} />
          <Route path="/graph" element={<GraphPage />} />
          <Route path="/products" element={sectionVisible('products') ? <ProductsPage /> : <SectionOff what="Products" />} />
          <Route path="/products/:id" element={sectionVisible('products') ? <ProductPage /> : <SectionOff what="Products" />} />
          <Route path="/modules" element={<ModulesPage />} />
          <Route path="/modules/:slug" element={<ModulePage />} />
          <Route
            path="/admin"
            element={
              <RequireCapability action="user.manage" what="Administration">
                <AdminPage />
              </RequireCapability>
            }
          />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </ErrorBoundary>
    </AppShell>
  );
}

/**
 * Remembered per tab: tokens live in session storage, so a new tab (Open in new tab, a link pasted
 * from chat) starts signed out. The first sign-in page a tab shows therefore goes straight to the
 * identity provider, which sends the user back without a word when the hosted-UI session is still
 * live. The button is for the second time, after an interrupted or refused sign-in.
 */
export const TRIED_KEY = 'air.signin.tried';

function tried(): boolean {
  try {
    return sessionStorage.getItem(TRIED_KEY) === '1';
  } catch {
    return true; // storage is blocked: never redirect, or a refusal could repeat
  }
}

function markTried(): void {
  try {
    sessionStorage.setItem(TRIED_KEY, '1');
  } catch {
    // nothing to remember it in; the button below still works
  }
}

/** Known sign-in failures as something to act on, instead of the OIDC library's own words. */
export function signInProblem(error: Error | undefined): string {
  if (!error) return '';
  if (/matching state|state not found|No state in response/i.test(error.message)) {
    return 'Sign-in was interrupted, so it could not be finished. Sign in again.';
  }
  if (/expired/i.test(error.message)) return 'The sign-in took too long and expired. Sign in again.';
  return error.message;
}

function SignIn() {
  const auth = useAuth();
  const location = useLocation();
  const target = isCallback(location.pathname) ? '/' : location.pathname + location.search;
  const signIn = React.useCallback(() => {
    markTried();
    void auth.signinRedirect({ state: target });
  }, [auth, target]);
  const auto = !auth.error && !tried();
  React.useEffect(() => {
    if (auto) signIn();
  }, [auto, signIn]);
  if (auto) return <PageLoading label={'Signing in\u2026'} />;
  const problem = signInProblem(auth.error);
  return (
    <div className="air-signin">
      <Container header={<Header variant="h1">{APP_NAME()}</Header>}>
        <SpaceBetween size="m">
          <Box>{tagline() || 'Track competitors, signals and trends in your market, and act on the gaps they reveal.'}</Box>
          {problem && <Box color="text-status-error">{problem}</Box>}
          <Button variant="primary" onClick={signIn}>
            Sign in
          </Button>
        </SpaceBetween>
      </Container>
    </div>
  );
}

export function Gate() {
  const auth = useAuth();
  if (auth.isLoading || auth.activeNavigator) return <PageLoading label={'Signing in\u2026'} />;
  if (!auth.isAuthenticated) return <SignIn />;
  return <AppRoutes />;
}

/** A data router, so pages with unsaved edits can block in-app navigation (useBlocker). */
export default function App() {
  const [router] = React.useState(() =>
    createBrowserRouter([
      // Opened from a digest email by someone who may not be signed in: outside the sign-in gate.
      {
        path: '/unsubscribe',
        element: (
          <ErrorBoundary>
            <UnsubscribePage />
          </ErrorBoundary>
        ),
      },
      {
        path: '*',
        element: (
          <ErrorBoundary>
            <Gate />
          </ErrorBoundary>
        ),
      },
    ]),
  );
  return <RouterProvider router={router} />;
}
