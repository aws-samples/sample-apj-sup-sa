// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import type { Edge, GraphBrief, Nav, Neighborhood } from '@/api/types';
import type { GraphEdge, GraphNode } from '@/components/graph/GraphView';

/** The canvas's node type for a record: a custom insight kind is ``insight:<kind>``. */
export function canvasType(n: Pick<GraphBrief, 'type' | 'insight_kind'>): string {
  return n.type === 'insight' && n.insight_kind ? `insight:${n.insight_kind}` : n.type;
}

export function edgeId(e: Pick<Edge, 'from' | 'type' | 'to'>): string {
  return `${e.from}|${e.type}|${e.to}`;
}

/**
 * A neighbourhood as the canvas draws it. Each node's "+N" is its connections minus those drawn,
 * so a node with more links than the picture shows says so.
 */
export function toCanvas(hood: Neighborhood): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const ids = new Set(hood.nodes.map((n) => n.id));
  const edges = hood.edges.filter((e) => ids.has(e.from) && ids.has(e.to));
  const drawn = new Map<string, number>();
  for (const e of edges) {
    drawn.set(e.from, (drawn.get(e.from) ?? 0) + 1);
    drawn.set(e.to, (drawn.get(e.to) ?? 0) + 1);
  }
  return {
    nodes: hood.nodes.map((n) => ({
      id: n.id,
      label: canvasType(n),
      properties: { title: n.title, summary: n.summary, category: n.category, updated_at: n.updated_at, evidence_url: n.evidence_url },
      degree: n.degree,
      hiddenNeighbourCount: Math.max(0, n.degree - (drawn.get(n.id) ?? 0)),
    })),
    edges: edges.map((e) => ({ id: edgeId(e), from: e.from, to: e.to, label: e.type, properties: { ...e } })),
  };
}

/** The names a deployment gives its own insight kinds, for the legend and the details panel. */
export function kindNames(nav: Nav | undefined): Record<string, string> {
  return Object.fromEntries((nav?.custom ?? []).map((c) => [`insight:${c.kind}`, c.label]));
}
