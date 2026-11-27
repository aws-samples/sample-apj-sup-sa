// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Popover from '@cloudscape-design/components/popover';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator, { type StatusIndicatorProps } from '@cloudscape-design/components/status-indicator';
import type { ReactNode } from 'react';
import type { ApiError } from '../api';
import type { CapSource, LimitState } from '../types';

export function TableEmpty({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <Box margin={{ vertical: 'xs' }} textAlign="center" color="inherit">
      <SpaceBetween size="xxs">
        <div>
          <b>{title}</b>
          {subtitle && (
            <Box variant="p" color="inherit">
              {subtitle}
            </Box>
          )}
        </div>
        {action}
      </SpaceBetween>
    </Box>
  );
}

export function ErrorState({ error, onRetry, what = 'data' }: { error: ApiError; onRetry?: () => void; what?: string }) {
  return (
    <Alert
      type="error"
      header={`Could not load ${what}`}
      action={onRetry && <Button onClick={onRetry}>Retry</Button>}
    >
      {error.message}
      {error.requestId && <div>Request ID: {error.requestId}</div>}
    </Alert>
  );
}

/** Error text for tables and charts (inside their own empty/error slot). */
export function InlineError({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <Box textAlign="center" color="inherit">
      <SpaceBetween size="xxs">
        <StatusIndicator type="error">{error.message}</StatusIndicator>
        {error.requestId && <Box variant="small">Request ID: {error.requestId}</Box>}
        {onRetry && <Button onClick={onRetry}>Retry</Button>}
      </SpaceBetween>
    </Box>
  );
}

const STATE_UI: Record<LimitState, { type: StatusIndicatorProps.Type; label: string }> = {
  ok: { type: 'success', label: 'OK' },
  warn: { type: 'warning', label: 'Warning' },
  critical: { type: 'error', label: 'Critical' },
  blocked: { type: 'stopped', label: 'Blocked' },
  none: { type: 'info', label: 'No cap' },
};

export function StateIndicator({ state }: { state: LimitState }) {
  const s = STATE_UI[state];
  return (
    <StatusIndicator type={s.type} colorOverride={state === 'blocked' ? 'red' : undefined}>
      {s.label}
    </StatusIndicator>
  );
}

export function sourceLabel(source: CapSource): string {
  if (source === null) return 'none';
  if (source === 'default') return 'env default';
  return source;
}

export function SourceBadge({ source }: { source: CapSource }) {
  const color = source === 'user' ? 'blue' : source?.startsWith('group:') ? 'green' : source === 'org' ? 'severity-low' : 'grey';
  return <Badge color={color}>{sourceLabel(source)}</Badge>;
}

export function Estimated({ children }: { children: ReactNode }) {
  return (
    <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
      <span>{children}</span>
      <Popover
        header="Estimated spend"
        size="medium"
        triggerType="custom"
        content={
          <SpaceBetween size="xs">
            <span>
              The gateway meters each request from its input and output token counts and the list price per 1M tokens on the
              Pricing page. Bedrock reports cached input as input, so cached prompts are priced at the input rate (an upper bound).
            </span>
            <span>
              Model spend reaches the ledger through usage_sync every 5 minutes, a few minutes after each call, so the latest minutes can read low. This is not your AWS bill;
              check Cost Explorer for billed amounts.
            </span>
          </SpaceBetween>
        }
      >
        <Badge color="grey">Estimated</Badge>
      </Popover>
    </SpaceBetween>
  );
}


