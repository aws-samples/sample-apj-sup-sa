// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import React from 'react';

/**
 * The one-node banner (ported from the CoP Tech Radar): which node the canvas is showing, how many
 * connections it has and, when they do not fit on one page, which neighbours are drawn, with Previous neighbours and
 * "More neighbours". Dismissing it goes back to the overview.
 */
export function GraphScopeBanner({
  title,
  kind,
  connections,
  page,
  onExit,
}: {
  title: string;
  kind: string;
  /** The node's full number of connections. */
  connections: number;
  /** Present when the neighbours are paged: ``total`` neighbours, this page's ``offset`` and size. */
  page?: { total: number; offset: number; pageSize: number; onPage: (offset: number) => void; loading?: boolean };
  onExit: () => void;
}): React.ReactElement {
  const paginated = page !== undefined && page.total > page.pageSize;
  let count = connections === 1 ? '1 connection' : `${connections} connections`;
  if (paginated) {
    const to = Math.min(page.offset + page.pageSize, page.total);
    count += ` \u00b7 showing neighbours ${page.offset + 1}\u2013${to} of ${page.total}`;
  }
  const hasPrev = paginated && page.offset > 0;
  const hasNext = paginated && page.offset + page.pageSize < page.total;
  return (
    <Alert type="info" dismissible onDismiss={onExit} dismissAriaLabel="Back to overview" data-testid="graph-scope-banner">
      <SpaceBetween direction="horizontal" size="xs" alignItems="center">
        <span>
          Showing relationships for <strong>{title}</strong> <Badge>{kind}</Badge>{' \u00b7 '}{count}
        </span>
        {paginated && (
          <SpaceBetween direction="horizontal" size="xxs">
            <Button
              variant="inline-link"
              iconName="angle-left"
              disabled={!hasPrev || page.loading}
              onClick={() => page.onPage(Math.max(0, page.offset - page.pageSize))}
              data-testid="scope-prev"
            >
              Previous neighbours
            </Button>
            <Button
              variant="inline-link"
              iconName="angle-right"
              iconAlign="right"
              disabled={!hasNext || page.loading}
              onClick={() => page.onPage(page.offset + page.pageSize)}
              data-testid="scope-next"
            >
              More neighbours
            </Button>
          </SpaceBetween>
        )}
      </SpaceBetween>
    </Alert>
  );
}
