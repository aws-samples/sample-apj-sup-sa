// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Flashbar, { type FlashbarProps } from '@cloudscape-design/components/flashbar';
import React from 'react';

import { ApiError } from '@/api/client';

/**
 * `key` identifies the message for de-duplication: a rich toast (one that carries a link) is a React
 * element, which cannot be compared, so two imports in a row used to stack two "Imported" toasts and
 * push an earlier error out of the four that are kept.
 */
type Notify = (type: 'success' | 'error' | 'info' | 'warning', content: React.ReactNode, key?: string) => void;

const NotifyContext = React.createContext<{ notify: Notify; fail: (err: unknown) => void; clearLinks: () => void }>({
  notify: () => undefined,
  fail: () => undefined,
  clearLinks: () => undefined,
});
const ItemsContext = React.createContext<FlashbarProps.MessageDefinition[]>([]);

/** The kinds of message that can carry a link to the next step (errors stay until dismissed). */
const LINK_TYPES: ReadonlySet<string> = new Set(['success', 'info']);

/** "in 40 seconds", "in 2 minutes", "tomorrow": how long a 429 or 503 says to wait. */
export function waitFor(seconds: number): string {
  if (seconds >= 72_000) return 'tomorrow';
  if (seconds >= 5400) return `in ${Math.round(seconds / 3600)} hours`;
  if (seconds >= 3600) return 'in an hour';
  if (seconds >= 120) return `in ${Math.ceil(seconds / 60)} minutes`;
  if (seconds >= 60) return 'in a minute';
  return seconds < 5 ? 'in a moment' : `in ${seconds} seconds`;
}

/** The local clock time `seconds` from now, "00:00" or "8:00 AM" as the browser writes times. */
export function reopensAt(seconds: number, now = Date.now()): string {
  return new Date(now + seconds * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/** What a failed action says once the user has put the session-expired dialog off. */
export const NOT_SAVED_EXPIRED =
  'That did not go through: your session has expired, so nothing was saved. Your changes are still on this page: download them from the banner at the top, or copy them, then sign in again.';
export const NOT_SAVED_DISABLED =
  'That did not go through: your account is disabled, so nothing was saved. Your changes are still on this page: download or copy them before you sign out.';

/** A sentence that already ends: a stop, then any closing brackets or quotes (scan cycle 51). */
const ENDED = /[.!?][)"'\]\u201d\u2019]*$/;

export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    // Inline messages built from this used to blame the file or the form with a raw "Unauthorized" (r3 F3-044).
    if (err.sessionEnded) return err.code === 'account_disabled' ? 'Your account is disabled, so this could not be done.' : 'Your session has expired: sign in again, then try again.';
    const extra = Array.isArray(err.details.errors) ? ` ${(err.details.errors as string[]).slice(0, 5).join('; ')}` : '';
    // A rate limit or a throttle arrives as a lower-case fragment ("too many requests; wait a minute
    // and try again") and says nothing about when to come back.
    const limited = err.status === 429 || err.status === 503;
    const message = limited ? err.message.charAt(0).toUpperCase() + err.message.slice(1) : err.message;
    // A closed daily budget opens again at UTC midnight (the wait is up to it): said as a clock time.
    const when =
      err.retryAfter === null
        ? ''
        : err.code === 'budget_exhausted'
          ? ` The daily budget opens again at midnight UTC, ${reopensAt(err.retryAfter)} your time.`
          : ` Try again ${waitFor(err.retryAfter)}.`;
    // A message with no end punctuation gets a full stop before the wait, so two sentences never run on (c50).
    // Trailing spaces are dropped, and a closing bracket or quote after the stop still counts (c51).
    const said = `${message}${extra}`.trimEnd();
    return `${said}${when && said && !ENDED.test(said) ? '.' : ''}${when}`;
  }
  return err instanceof Error ? err.message : String(err);
}

/**
 * Holds the app's notifications. With `inline` (the default) the Flashbar is drawn above the
 * children; the app shell passes `inline={false}` and puts <NotificationBar> into AppLayout's
 * sticky notifications slot instead, so a message never pushes the page down.
 */
export function NotificationProvider({ children, inline = true }: { children: React.ReactNode; inline?: boolean }) {
  const [items, setItems] = React.useState<FlashbarProps.MessageDefinition[]>([]);
  const counter = React.useRef(0);
  const timers = React.useRef(new Set<ReturnType<typeof setTimeout>>());
  React.useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((t) => clearTimeout(t)); // no state updates after unmount
  }, []);
  const notify = React.useCallback<Notify>((type, content, key) => {
    // The id carries the de-duplication key: "<key>#<counter>", with an empty key for a message that
    // has none. A rich toast (a React element) cannot be compared by content, so it needs the key.
    const mark = (key ?? (typeof content === 'string' ? content : '')).replace(/#/g, '');
    const id = `${mark}#${++counter.current}`;
    const dismiss = () => setItems((xs) => xs.filter((x) => x.id !== id));
    // Newest first: a stacked Flashbar shows only its first item until it is expanded. A message
    // already showing (a burst of the same error) moves to the top instead of stacking again.
    const same = (x: FlashbarProps.MessageDefinition) => x.type === type && mark !== '' && String(x.id).startsWith(`${mark}#`);
    setItems((xs) => [{ id, type, content, dismissible: true, onDismiss: dismiss }, ...xs.filter((x) => !same(x)).slice(0, 3)]);
    // Only a plain message times out: one that carries a link is the only way to the next step, and a
    // keyboard or screen-reader user often cannot reach it in six seconds.
    if ((type === 'success' || type === 'info') && typeof content === 'string') {
      const timer = setTimeout(() => {
        timers.current.delete(timer);
        dismiss();
      }, 6000);
      timers.current.add(timer);
    }
  }, []);
  const fail = React.useCallback(
    (err: unknown) => {
      // A 401 already opens "Your session has expired", and every page that was loading at the time
      // would otherwise stack an "Unauthorized" toast behind that dialog. After "Not now" no dialog
      // opens, so the failure must be said here: a Save that stops without a word looks like it worked.
      if (err instanceof ApiError && err.sessionEnded) {
        if (!err.announced) notify('error', err.code === 'account_disabled' ? NOT_SAVED_DISABLED : NOT_SAVED_EXPIRED);
        return;
      }
      notify('error', describeError(err));
    },
    [notify],
  );
  /**
   * Drops the success and info messages that carry a link: they never time out, and on a phone they
   * stayed pinned over the next page too (r3 F3-030). The shell calls this when the page changes.
   */
  const clearLinks = React.useCallback(() => setItems((xs) => xs.filter((x) => !(LINK_TYPES.has(String(x.type)) && typeof x.content !== 'string'))), []);
  const value = React.useMemo(() => ({ notify, fail, clearLinks }), [notify, fail, clearLinks]);
  return (
    <NotifyContext.Provider value={value}>
      <ItemsContext.Provider value={items}>
        {inline && <NotificationBar />}
        {children}
      </ItemsContext.Provider>
    </NotifyContext.Provider>
  );
}

/** The Flashbar of the nearest NotificationProvider. */
export function NotificationBar() {
  const items = React.useContext(ItemsContext);
  return <Flashbar items={items} stackItems />;
}

export function useNotify() {
  return React.useContext(NotifyContext);
}
