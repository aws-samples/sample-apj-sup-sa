// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import React from 'react';

interface Props {
  header: string;
  children: React.ReactNode;
  /** Runs the deletion; the caller closes the dialog (or navigates away) when it settles. */
  onConfirm: () => Promise<unknown>;
  onDismiss: () => void;
  /** For the biggest deletes: Delete stays off until this exact text is typed (CTR QA-089). */
  confirmText?: string;
}

/** Deletes cannot be undone, so every one goes through this dialog first. */
export default function ConfirmDelete({ header, children, onConfirm, onDismiss, confirmText }: Props) {
  const [busy, setBusy] = React.useState(false);
  const [typed, setTyped] = React.useState('');
  const typedOk = confirmText === undefined || typed === confirmText;
  const confirm = () => {
    setBusy(true);
    void onConfirm().finally(() => setBusy(false));
  };
  return (
    <Modal
      visible
      // While the delete runs, closing the dialog would hide whether it worked: it stays until it settles.
      onDismiss={() => !busy && onDismiss()}
      header={header}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" disabled={busy} onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={!typedOk} onClick={confirm}>Delete</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <div>{children}</div>
        {confirmText !== undefined && (
          <FormField
            label={`Type ${confirmText} to confirm`}
            errorText={typed && !typedOk ? `Must be exactly ${confirmText} (case-sensitive)` : undefined}
          >
            <Input value={typed} onChange={({ detail }) => setTyped(detail.value)} ariaLabel={`Type ${confirmText} to confirm`} disabled={busy} />
          </FormField>
        )}
      </SpaceBetween>
    </Modal>
  );
}
