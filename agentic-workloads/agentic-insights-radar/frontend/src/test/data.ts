// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import type { GraphNode, InsightDetail, Module, Product, RadarSchema, RadarView } from '@/api/types';

export function node(over: Partial<GraphNode> = {}): GraphNode {
  return {
    id: 'signal/1',
    type: 'signal',
    category: 'output',
    title: 'Agent sandboxing standardises',
    summary: 'Docker takes its spec to the CNCF',
    description: '<p>Docker is <strong>taking</strong> its spec to the CNCF.</p>',
    attributes: {},
    aliases: [],
    created_by: 'module:signal',
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
    version: 1,
    degree: 3,
    confidence: 0.7,
    module: 'signal',
    ...over,
  };
}

export function detail(over: Partial<InsightDetail> = {}): InsightDetail {
  const empty = { supports: [], contradicts: [], about: [], addresses: [], addressed_by: [], supported_by: [], drafts: [], other: [] };
  return {
    node: node(),
    evidence: {
      ...empty,
      supports: [{ edge: 'supports', direction: 'out', node: { id: 'fact/1', type: 'fact', title: 'Docker news', summary: 'InfoQ', updated_at: '2026-10-01T10:00:00Z', evidence_url: 'https://infoq.example.com/a' } }],
      about: [{ edge: 'about', direction: 'out', node: { id: 'product/1', type: 'product', title: 'AgentCore', summary: '', updated_at: '2026-10-01T10:00:00Z' } }],
    },
    feedback: { up: 2, down: 0, mine: null },
    ...over,
  };
}

export const SCHEMA: RadarSchema = {
  sectors: [{ name: 'Direct' }, { name: 'Adjacent' }],
  rings: [{ name: 'Leader' }, { name: 'Challenger' }],
  markers: [{ name: 'Gaining', glyph: 'triangle' }],
  sector_label: 'Category',
  ring_label: 'Position',
  marker_label: 'Momentum',
};

export function radarView(over: Partial<RadarView> = {}): RadarView {
  return {
    radar: {
      slug: 'rivals',
      name: 'Rivals',
      schema: SCHEMA,
      purpose: 'Who competes with us',
      description: '',
      mode: 'assist',
      auto_apply_days: 7,
      owner: 'u-admin',
      owner_email: 'admin@example.com',
      editors: ['ed@example.com'],
      current_version: 2,
      entry_count: 1,
      created_at: '2026-10-01T10:00:00Z',
      updated_at: '2026-10-01T10:00:00Z',
      updated_by: 'user:u-admin',
      version_lock: 3,
    },
    version: 2,
    schema: SCHEMA,
    entries: [{ key: 'k1', title: 'Acme', sector: 'Direct', ring: 'Leader', marker: 'Gaining', description: 'Market leader', node_id: 'organization/1', link: 'https://acme.example.com' }],
    can_edit: true,
    can_admin: true,
    ...over,
  };
}

export function product(over: Partial<Product> = {}): Product {
  return {
    id: 'p1',
    name: 'DeskPro',
    summary: 'Standing desk',
    capabilities: ['dual motor'],
    segments: ['home office'],
    competitors: ['Acme'],
    doc_urls: ['https://docs.example.com/'],
    notes: 'Premium segment',
    web_status: 'ready',
    web_pages: 12,
    web_crawled_at: '2026-10-01T10:00:00Z',
    created_by: 'u',
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
    version: 1,
    stats: { crawl: { pages: 12, stopped: 'page_limit' } },
    ...over,
  };
}

export function module(over: Partial<Module> = {}): Module {
  return {
    slug: 'scout',
    name: 'Scout',
    template: 'scout',
    output_kind: null,
    description: 'Crawls your sources',
    instructions: 'Agent platforms',
    insight_kind: null,
    insight_label: null,
    input_types: [],
    grants: ['kg_read', 'kg_write_evidence', 'sources'],
    product_id: null,
    model_key: 'sonnet-5-5',
    interval_minutes: 360,
    daily_token_budget: 2_000_000,
    max_concurrency: 1,
    mcp_servers: [],
    enabled: false,
    builtin: true,
    created_by: 'system',
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
    version: 1,
    budget: { consumed: 500_000, limit: 2_000_000, state: 'open' },
    ...over,
  };
}
