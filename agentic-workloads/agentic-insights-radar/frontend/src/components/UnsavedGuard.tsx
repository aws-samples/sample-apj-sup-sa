// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import React from 'react';
import { useBlocker } from 'react-router-dom';

import { type Rescue, useReportDirty } from '@/lib/dirty';

/**
 * Warns before unsaved work is thrown away: the browser asks before a reload or a closed tab, an
 * in-app navigation to another page asks too, and Sign out reads the same flag through the registry.
 * `children` is the explanation; every page that holds a draft renders one of these. `rescue` is
 * offered where the page must be left at once, such as signing in again after the session expired.
 */
export default function UnsavedGuard({ dirty, rescue, children }: { dirty: boolean; rescue?: Rescue; children: React.ReactNode }) {
  useReportDirty(dirty, rescue);
  React.useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault(); // the browser asks before leaving
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  // Changing the query on the same page (a tab, a filter) is not leaving it.
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  const leaving = blocker.state === 'blocked' ? blocker : null;
  if (!leaving) return null;
  return (
    <Modal
      visible
      onDismiss={leaving.reset}
      header="Leave without saving?"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={leaving.reset}>Stay on this page</Button>
            <Button variant="primary" onClick={leaving.proceed}>Leave and lose changes</Button>
          </SpaceBetween>
        </Box>
      }
    >
      {children}
    </Modal>
  );
}

/**
 * The warning a dialog with unsaved fields shows instead of closing, as Create radar does: Esc or a
 * click on the backdrop must not throw away 2,000 characters of instructions.
 */
export function DiscardWarning({ what, onKeep, onDiscard }: { what: string; onKeep: () => void; onDiscard: () => void }) {
  return (
    <Alert
      type="warning"
      header={`Discard ${what}?`}
      action={
        <SpaceBetween direction="horizontal" size="xs">
          <Button formAction="none" onClick={onKeep}>Keep editing</Button>
          <Button formAction="none" onClick={onDiscard}>Discard</Button>
        </SpaceBetween>
      }
    >
      What you entered is lost if you close the dialog now.
    </Alert>
  );
}
