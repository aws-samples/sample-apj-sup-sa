// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { useEffect, useState, type ReactNode } from 'react';

/** Impact confirmation for wide or destructive changes. With confirmWord set, the admin must type it. */
export function ConfirmModal({
  visible,
  header,
  impact,
  children,
  confirmWord,
  confirmLabel = 'Confirm',
  loading,
  onConfirm,
  onCancel,
}: {
  visible: boolean;
  header: string;
  impact: ReactNode;
  children?: ReactNode;
  confirmWord?: string;
  confirmLabel?: string;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState('');
  useEffect(() => {
    if (visible) setTyped('');
  }, [visible]);
  const ok = !confirmWord || typed === confirmWord;
  return (
    <Modal
      visible={visible}
      onDismiss={onCancel}
      header={header}
      closeAriaLabel="Close"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onCancel}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!ok} loading={loading} onClick={onConfirm}>
              {confirmLabel}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <Alert type="warning" header="Impact">
          {impact}
        </Alert>
        {children}
        {confirmWord && (
          <FormField label={`To confirm, type "${confirmWord}"`}>
            <Input value={typed} onChange={({ detail }) => setTyped(detail.value)} placeholder={confirmWord} ariaRequired />
          </FormField>
        )}
      </SpaceBetween>
    </Modal>
  );
}
