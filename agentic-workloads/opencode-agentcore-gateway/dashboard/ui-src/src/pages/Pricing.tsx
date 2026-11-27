// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useApi } from '../api';
import type { Models } from '../types';
import { DataTable, Page } from './_kit';

const p = (n?: number) => (n === undefined ? '-' : `$${n.toFixed(3)}`);
export default function Pricing() {
  const r = useApi<Models>('/api/v2/models');
  return (
    <Page title="Pricing" description="USD per 1M tokens used to meter spend (AWS Price List and billed rates, set in the deployment).">
      <DataTable title="Models" items={r.data?.models ?? []} load={r} columns={[
        { id: 'id', header: 'Model ID', cell: m => m.id }, { id: 'n', header: 'Name', cell: m => m.display_name },
        { id: 'i', header: 'Input', cell: m => p(m.price.input) }, { id: 'o', header: 'Output', cell: m => p(m.price.output) },
      ]} />
      <DataTable title="Aliases" items={Object.entries(r.data?.aliases ?? {})} load={r} columns={[
        { id: 'a', header: 'Alias (what the client sends)', cell: ([a]) => a }, { id: 't', header: 'Model it routes to', cell: ([, t]) => t }]} />
    </Page>
  );
}
