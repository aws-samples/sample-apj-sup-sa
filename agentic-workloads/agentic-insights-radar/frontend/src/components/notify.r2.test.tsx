// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** F2-012: an ended session is one dialog, not a toast per request. */
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ApiError } from '@/api/client';

import { NotificationProvider, useNotify } from './Notifications';

function Failing({ error }: { error: unknown }) {
  const { fail } = useNotify();
  return (
    <button type="button" onClick={() => fail(error)}>
      go
    </button>
  );
}

describe('the error toast', () => {
  it('says nothing for a 401, which already has its own dialog', async () => {
    render(<NotificationProvider><Failing error={new ApiError(401, 'unauthorized', 'Unauthorized')} /></NotificationProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'go' }));
    await waitFor(() => expect(createWrapper(document.body).findFlashbar()!.findItems()).toHaveLength(0));
  });

  it('still reports a 403, which has no dialog', async () => {
    render(<NotificationProvider><Failing error={new ApiError(403, 'forbidden', 'You cannot do that')} /></NotificationProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'go' }));
    expect(await screen.findByText('You cannot do that')).toBeInTheDocument();
  });
});

describe('r3 F3-030: clearLinks outside a provider', () => {
  it('does nothing, as notify and fail do', () => {
    function Bare() {
      const { clearLinks } = useNotify();
      return <button type="button" onClick={clearLinks}>clear</button>;
    }
    render(<Bare />);
    fireEvent.click(screen.getByRole('button', { name: 'clear' }));
    expect(screen.getByRole('button', { name: 'clear' })).toBeInTheDocument();
  });
});
