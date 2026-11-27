// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { WebStorageStateStore } from 'oidc-client-ts';
import { appName } from '@/lib/profile';
import React from 'react';
import { AuthProvider, useAuth } from 'react-oidc-context';

import { ApiClient } from '@/api/client';
import type { AppConfig, Me } from '@/api/types';
import { forgetChat } from '@/components/conversation';
import { useAsync } from '@/hooks/useAsync';
import { useAnyDirty, useRescues } from '@/lib/dirty';

/**
 * Sign-in: OpenID Connect authorization code flow with PKCE against the Cognito user pool. The
 * app sends the ID token (it carries email and groups, which the API needs) to /api and to the
 * chat runtime. Tokens live in session storage: closing the tab signs out of the app.
 */
export const CALLBACK_PATH = '/auth/callback';

/** How often a visible tab re-reads /api/config (the AI kill switch lives there). */
export const CONFIG_REFRESH_MS = 60_000;

/** How soon a failed background re-read of /api/me is tried again (the last good profile is kept meanwhile). */
export const ME_RETRY_MS = 15_000;

export function oidcSettings(config: AppConfig, origin: string) {
  return {
    authority: `https://cognito-idp.${config.region}.amazonaws.com/${config.user_pool_id}`,
    client_id: config.user_pool_client_id,
    redirect_uri: `${origin}${CALLBACK_PATH}`,
    post_logout_redirect_uri: `${origin}/`,
    response_type: 'code',
    scope: 'openid email profile',
    ...(config.identity_provider ? { extraQueryParams: { identity_provider: config.identity_provider } } : {}),
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    // Drop ?code&state from the address bar; <AfterSignIn> then routes to the original page.
    onSigninCallback: () => window.history.replaceState({}, document.title, window.location.pathname),
  };
}

/** Revokes a refresh token at Cognito, so a copy of it cannot mint new ID tokens after sign-out
 * (Cognito's /logout ends only the hosted-UI session). A failed revocation never blocks sign-out. */
export async function revokeRefreshToken(config: AppConfig, token: string): Promise<void> {
  try {
    await fetch(`https://${config.cognito_domain}/oauth2/revoke`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ token, client_id: config.user_pool_client_id }).toString(),
      signal: AbortSignal.timeout(3000), // sign-out must not wait on a stalled connection
    });
  } catch {
    // offline or blocked: the local session is still cleared and the refresh token expires within 12 hours (10 when federated)
  }
}

/** Cognito's logout endpoint takes client_id and logout_uri (not the standard end-session form). */
export function logoutUrl(config: AppConfig, origin: string): string {
  return `https://${config.cognito_domain}/logout?client_id=${encodeURIComponent(config.user_pool_client_id)}&logout_uri=${encodeURIComponent(`${origin}/`)}`;
}

interface Session {
  config: AppConfig;
  /** Re-read /api/config, e.g. after an administrator flips the AI kill switch. */
  refreshConfig: () => void;
  api: ApiClient;
  me: Me | null;
  /** True until /api/me has answered for the signed-in user. */
  meLoading: boolean;
  /**
   * Why the profile could not be read. With no profile at all every `can()` is false, so pages must
   * not be shown; when a background re-read fails, `me` keeps the last profile that was read.
   */
  meError: Error | undefined;
  refreshMe: () => void;
  /** The session has expired (a 401, or no token to send): ask the user to sign in again. */
  expire: () => void;
  /**
   * True once the session has ended, also after "Not now" on its dialog: pages stop polling instead
   * of asking again every 15 seconds (r3 F3-003).
   */
  sessionEnded: boolean;
  /**
   * The session ended and the user put its dialog off ("Not now"): the shell then keeps a banner
   * with the rescue downloads and the way to sign in again (or out), since no dialog opens again.
   */
  signedOut?: 'expired' | 'disabled' | null;
  /** Leaves for the identity provider and comes back to this page. */
  signInAgain?: () => void;
  signOut: () => Promise<void>;
  idToken: () => string | undefined;
}

const SessionContext = React.createContext<Session | null>(null);

export function useSession(): Session {
  const value = React.useContext(SessionContext);
  if (!value) throw new Error('useSession outside <SessionProvider>');
  return value;
}

export function can(me: Me | null, action: string): boolean {
  return !!me && me.can.includes(action);
}

/**
 * Whether the caller may read what is written from private product documents (gaps, content ideas,
 * drafts, private records): the API keeps them from anyone without `product.read`.
 */
export function seesPrivate(me: Me | null): boolean {
  return can(me, 'product.read');
}

function SessionInner({ config: initialConfig, children }: { config: AppConfig; children: React.ReactNode }) {
  const auth = useAuth();
  const idToken = auth.user?.id_token;
  // One client per session: token renewals only update what the client reads, so `api` keeps
  // its identity and pages keyed on it do not refetch every time the token is renewed.
  const [tokens] = React.useState(() => {
    let current = idToken;
    return { get: () => current, set: (t: string | undefined) => void (current = t) };
  });
  // A layout effect: React runs every layout effect before any passive one, so pages that fetch in
  // useEffect on the same commit (their first load after sign-in) already see the token.
  React.useLayoutEffect(() => tokens.set(idToken), [tokens, idToken]);
  const getToken = tokens.get;
  const [api] = React.useState(() => new ApiClient(tokens.get));
  const [config, setConfig] = React.useState(initialConfig);
  const refreshConfig = React.useCallback(() => void api.config().then(setConfig, () => undefined), [api]);
  const subject = auth.user?.profile.sub;
  const meState = useAsync(() => (subject ? api.me() : Promise.resolve(null)), [subject]);
  // The profile is re-read in the background (focus, every minute, after a 403). A failed re-read used
  // to leave no profile at all, so the shell replaced the page, unsaved drafts included. useAsync keeps
  // the last profile read for this user through a failed re-read; a different user (or none) starts again.
  const me = meState.data ?? null;
  const [expired, setExpired] = React.useState(false);
  const [disabled, setDisabled] = React.useState(false);
  /** "Not now" on the disabled-account dialog, chosen to copy unsaved changes before signing out. */
  const [disabledLater, setDisabledLater] = React.useState(false);
  const disabledQuiet = React.useRef(false);
  /**
   * "Not now" holds the dialog back until the session works again. Without it every later 401
   * re-opened it, and a page that polls (a product every 15 s, a draft every 20 s, module runs every
   * 15 s) brought it back by itself while the user was trying to copy their work out.
   */
  const silenced = React.useRef(false);
  React.useEffect(() => {
    silenced.current = false; // a renewed token is a working session again
  }, [idToken]);
  /** The same, as state, so pages and the shell see it: the token it was chosen for, so a renewed one ends it. */
  const [quietFor, setQuietFor] = React.useState<string | undefined | null>(null);
  const quiet = quietFor !== null && quietFor === idToken;
  /** "Not now" (and Esc, which used to re-open the dialog at the next 401: r3 F3-040). */
  const notNow = () => {
    silenced.current = true;
    setQuietFor(idToken);
    setExpired(false);
  };
  // Signing in again leaves this page, and the gate replaces it while the redirect runs.
  const anyDirty = useAnyDirty();
  const rescues = useRescues();
  const expire = React.useCallback(() => setExpired(true), []);
  React.useEffect(() => {
    // A disabled or deleted account is not an expired session: signing in again would only loop.
    // It answers whether a dialog tells the user: after "Not now" none does, and the failed action says so instead.
    api.setUnauthorizedHandler((code) => {
      if (code === 'account_disabled') {
        setDisabled(true);
        return !disabledQuiet.current;
      }
      if (silenced.current) return false;
      setExpired(true);
      return true;
    });
  }, [api]);
  // /api/config carries the AI kill switch, which any administrator can flip at any time. Without
  // this, every other open tab keeps the state it started with: a stale "AI features are paused"
  // banner with disabled buttons, or live buttons that fail.
  React.useEffect(() => {
    api.setGenaiDisabledHandler(refreshConfig);
  }, [api, refreshConfig]);
  // A 403 means this account may no longer be allowed what the page offered: read the profile again,
  // so the buttons match the answer rather than failing one after another.
  const refreshMe = meState.reload;
  React.useEffect(() => {
    api.setForbiddenHandler(refreshMe);
  }, [api, refreshMe]);
  // A failed re-read is tried again soon, not only at the next minute or focus. Every failure is a new
  // error, so it is tried again after each one, waiting twice as long each time (r3 F3-045: the timer
  // used to fire once, because the flag stayed true between failures). Not while the session is over.
  const retryError = meState.error && me && !expired && !quiet ? meState.error : null;
  const failures = React.useRef(0);
  const reading = meState.loading;
  React.useEffect(() => {
    if (!retryError) {
      if (!reading) failures.current = 0; // a re-read that worked (not one still on its way) starts the count again
      return undefined;
    }
    const delay = Math.min(ME_RETRY_MS * 2 ** failures.current, 8 * ME_RETRY_MS);
    failures.current += 1;
    const timer = setTimeout(refreshMe, delay);
    return () => clearTimeout(timer);
  }, [retryError, refreshMe, reading]);
  React.useEffect(() => {
    const refresh = () => {
      if (document.visibilityState !== 'visible') return;
      refreshConfig();
      refreshMe(); // an administrator can change a role at any time, not only the kill switch
    };
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    const timer = setInterval(refresh, CONFIG_REFRESH_MS);
    return () => {
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
      clearInterval(timer);
    };
  }, [refreshConfig, refreshMe]);
  const signinRedirect = auth.signinRedirect;
  const signInAgain = React.useCallback(
    () => void signinRedirect({ state: window.location.pathname + window.location.search }),
    [signinRedirect],
  );
  const signOut = React.useCallback(async () => {
    const refresh = auth.user?.refresh_token;
    if (refresh) await revokeRefreshToken(config, refresh);
    await auth.removeUser();
    forgetChat(); // the tab's chat goes with the session: the next person to sign in here must not read it
    window.location.assign(logoutUrl(config, window.location.origin));
  }, [auth, config]);
  const value = React.useMemo(
    () => ({
      config,
      refreshConfig,
      api,
      me,
      meLoading: meState.loading,
      meError: meState.error,
      refreshMe,
      expire,
      sessionEnded: expired || disabled || quiet,
      signedOut: disabled && disabledLater ? ('disabled' as const) : quiet && !expired && !disabled ? ('expired' as const) : null,
      signInAgain,
      signOut,
      idToken: getToken,
    }),
    [config, refreshConfig, api, me, meState.loading, meState.error, refreshMe, expire, expired, disabled, disabledLater, quiet, signInAgain, signOut, getToken],
  );
  return (
    <SessionContext.Provider value={value}>
      {children}
      {/* Only a signed-in user can have an expired session; signed out, the sign-in page shows instead. */}
      <Modal
        visible={expired && !disabled && auth.isAuthenticated}
        onDismiss={notNow}
        header="Your session has expired"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={notNow}>
                Not now
              </Button>
              {rescues.map((r) => (
                <Button key={r.label} iconName="download" onClick={r.run}>{r.label}</Button>
              ))}
              <Button variant="primary" onClick={signInAgain}>{anyDirty ? 'Sign in again and lose changes' : 'Sign in again'}</Button>
            </SpaceBetween>
          </Box>
        }
      >
        {/* "Download them first" only where a page offers a download (r3 F3-039). */}
        {anyDirty
          ? `A page has unsaved changes. They cannot be saved until you sign in again, and signing in leaves this page straight away and loses them. ${
              rescues.length ? 'Download them first, or choose Not now to copy them.' : 'Choose Not now to copy them out first.'
            } The page then keeps a banner with the way to sign in again.`
          : 'Sign in again to keep working. Signing in leaves this page, so choose Not now first if you need to copy unsaved changes.'}
      </Modal>
      {/* No onDismiss: Esc or a click on the backdrop used to leave an app where every request fails,
          and the dialog came back on the next 401 anyway. Sign out is the only way on. */}
      {/* With unsaved changes on a page, signing out at once would lose them: "Not now" leaves the
          page in place so they can be copied, and the account menu's Sign out then asks first. */}
      <Modal
        visible={disabled && !disabledLater && auth.isAuthenticated}
        header="Your account is disabled"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              {anyDirty && <Button
                  variant="link"
                  onClick={() => {
                    disabledQuiet.current = true;
                    setDisabledLater(true);
                  }}
                >
                  Not now
                </Button>}
              <Button variant="primary" onClick={() => void signOut()}>{anyDirty ? 'Sign out and lose changes' : 'Sign out'}</Button>
            </SpaceBetween>
          </Box>
        }
      >
        {`An administrator has disabled or removed your ${appName()} account, so signing in again does not help. Ask an administrator to turn it back on.${
          anyDirty ? ' Unsaved changes on this page can no longer be saved: choose Not now to copy them first, then sign out from the account menu.' : ''
        }`}
      </Modal>
    </SessionContext.Provider>
  );
}

export function SessionProvider({ config, children }: { config: AppConfig; children: React.ReactNode }) {
  const settings = React.useMemo(() => oidcSettings(config, window.location.origin), [config]);
  return (
    <AuthProvider {...settings}>
      <SessionInner config={config}>{children}</SessionInner>
    </AuthProvider>
  );
}

/** Test seam: provide a session without OIDC. */
export function TestSessionProvider({ value, children }: { value: Session; children: React.ReactNode }) {
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
