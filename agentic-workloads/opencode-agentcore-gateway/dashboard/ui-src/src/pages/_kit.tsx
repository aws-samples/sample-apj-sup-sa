// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Table, { type TableProps } from '@cloudscape-design/components/table';
import TextFilter from '@cloudscape-design/components/text-filter';
import { type ReactNode, useState } from 'react';
import type { Loadable } from '../api';
import { ErrorState, TableEmpty } from '../components/common';

export function Page({ title, description, actions, children }: { title: string; description?: string; actions?: ReactNode; children: ReactNode }) {
  return <ContentLayout header={<Header variant="h1" description={description} actions={actions}>{title}</Header>}>{children}</ContentLayout>;
}

export function DataTable<T>({ title, items, columns, load, filterText, actions, empty = 'No data' }: {
  title: string; items: T[]; columns: TableProps.ColumnDefinition<T>[]; load?: Loadable<unknown>;
  filterText?: (t: T) => string; actions?: ReactNode; empty?: string;
}) {
  const [q, setQ] = useState('');
  if (load?.error) return <ErrorState error={load.error} onRetry={load.reload} />;
  const shown = filterText && q ? items.filter(i => filterText(i).toLowerCase().includes(q.toLowerCase())) : items;
  return (
    <Table<T> variant="container" items={shown} columnDefinitions={columns} loading={load?.loading} loadingText="Loading" wrapLines
      header={<Header counter={`(${shown.length})`} actions={actions}>{title}</Header>}
      filter={filterText ? <TextFilter filteringText={q} filteringPlaceholder="Find" onChange={e => setQ(e.detail.filteringText)} /> : undefined}
      empty={<TableEmpty title={empty} />} />
  );
}

