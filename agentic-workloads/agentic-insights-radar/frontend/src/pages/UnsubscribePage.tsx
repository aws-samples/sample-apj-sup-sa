// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import React from 'react';
import { useSearchParams } from 'react-router-dom';

import { ApiClient, ApiError } from '@/api/client';
import { waitFor } from '@/components/Notifications';
import { usePageTitle } from '@/lib/pageTitle';

/** Signed out: the link in an email carries its own signed token, so no sign-in is sent. */
const PUBLIC_API = new ApiClient(() => undefined);

type State = { step: 'ready' | 'busy' | 'done' } | { step: 'failed'; message: string; again: boolean };

/**
 * Worth another try: no answer, a throttle, a server error, or a 403 (the unsubscribe API never sends one, so it
 * is a WAF or gateway block, which a rate rule lifts; c23). Every other 4xx is a refusal of the request itself,
 * which the API page also calls a bad link (scan cycle 53). The statuses 401, 404 and 410 and the code
 * unsubscribe_unavailable (a 503 only an operator can fix) have their own texts below, checked first (scan cycles
 * 49 to 56).
 */
function worthRetry(err: unknown): boolean {
  if (!(err instanceof ApiError)) return true;
  return err.status === 0 || err.status === 403 || err.status === 429 || err.status >= 500;
}

/**
 * The one text for every failure worth another try (see worthRetry), never an error message spliced after a
 * prefix (scan cycles 48 to 53). Switching the delivery off again is harmless, so trying again is safe even when
 * the first post may have landed (a timeout or a gateway error).
 */
function notConfirmed(err: unknown): string {
  const wait = err instanceof ApiError && err.retryAfter !== null ? ` ${waitFor(err.retryAfter)}` : '';
  return (
    `We could not confirm that you are unsubscribed. Unsubscribing twice is harmless, so try again${wait}, ` +
    "or sign in and turn the delivery off under What's new."
  );
}

/**
 * The page an email's unsubscribe link opens. Opening it changes nothing (mail scanners follow links):
 * only the button posts the token, which turns off that one delivery.
 */
export default function UnsubscribePage({ api = PUBLIC_API }: { api?: ApiClient }) {
  usePageTitle('Unsubscribe');
  const [params] = useSearchParams();
  const token = params.get('t') ?? '';
  const [state, setState] = React.useState<State>({ step: 'ready' });
  const confirm = () => {
    setState({ step: 'busy' });
    api.unsubscribe(token).then(
      () => setState({ step: 'done' }),
      (err: unknown) =>
        setState(
          // The API pages' texts, with What's new as a place in this app rather than a link (scan cycles 42 to 44).
          err instanceof ApiError && err.status === 404
            ? { step: 'failed', again: false, message: 'Unsubscribe links are not used on this deployment, so there is nothing to turn off.' }
            : err instanceof ApiError && err.status === 401
            ? // The public route never answers 401 (a bad token is a 400): the route is gone because email is off,
              // and the request fell to the signed-in API (scan cycle 54).
              { step: 'failed', again: false, message: "Unsubscribing by link is switched off on this deployment. If digests still reach you, sign in and turn the delivery off under What's new." }
            : err instanceof ApiError && err.status === 410
            ? { step: 'failed', again: false, message: "This unsubscribe link has expired. To stop the email, sign in and turn the delivery off under What's new." }
            : err instanceof ApiError && err.code === 'unsubscribe_unavailable'
            ? // Only an operator can fix it, so no Try again (scan cycle 49).
              { step: 'failed', again: false, message: "Unsubscribing is not available right now. To stop the email, sign in and turn the delivery off under What's new." }
            : !worthRetry(err)
            ? { step: 'failed', again: false, message: "This unsubscribe link is incomplete or no longer valid. Open the link in the email again, or sign in and turn the delivery off under What's new." }
            : { step: 'failed', again: true, message: notConfirmed(err) },
        ),
    );
  };
  let body: React.ReactNode;
  if (!token) {
    body = <Box>This unsubscribe link is incomplete or no longer valid. Open the link in the email again, or sign in and turn the delivery off under What&apos;s new.</Box>;
  } else if (state.step === 'done') {
    body = (
      <div role="status">
        <StatusIndicator type="success">You are unsubscribed. This digest email will not be sent to you again; your other deliveries are unchanged.</StatusIndicator>
      </div>
    );
  } else {
    body = (
      <SpaceBetween size="m">
        <Box>Stop this digest email? Only this one delivery is turned off. You can turn it back on after signing in, under What&apos;s new.</Box>
        {state.step === 'failed' && (
          <div role="alert">
            <StatusIndicator type="error">{state.message}</StatusIndicator>
          </div>
        )}
        {(state.step !== 'failed' || state.again) && (
          <Button variant="primary" loading={state.step === 'busy'} onClick={confirm}>
            {state.step === 'failed' ? 'Try again' : 'Unsubscribe'}
          </Button>
        )}
      </SpaceBetween>
    );
  }
  return (
    <div className="air-signin">
      <Container header={<Header variant="h1">Unsubscribe from the digest</Header>}>{body}</Container>
    </div>
  );
}
