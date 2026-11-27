// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Test stub for `react-force-graph-2d`. The real library draws to a canvas, which jsdom does not
 * have. This stub renders the nodes and links as buttons wired to the same callbacks, and offers
 * two extra buttons so a test can stand in for the library's own work:
 *
 * - `fg-layout` places every node and swaps each link's ends for node objects, as the layout does;
 * - `fg-paint` calls every paint and style callback on a recording 2D context at `stubControl.scale`.
 *
 * `stubControl` also sets what the imperative handle answers (its zoom, whether it has methods).
 */
import React from 'react';

// Loose by design: the real component has about 100 props and the tests touch a few.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyProps = Record<string, any>;

export const stubControl = {
  /** The globalScale the paint button paints at. */
  scale: 1,
  /** What `zoom()` returns. */
  zoom: 1,
  /** An imperative handle with no methods at all. */
  bare: false,
  /** `d3Force('link')` answers nothing. */
  noLink: false,
  /** `fg-layout` places the nodes (false: it only swaps the link ends for node objects). */
  place: true,
  /** Every call the recording context received, as `name` or `name=value`. */
  calls: [] as string[],
  reset() {
    this.scale = 1; this.zoom = 1; this.bare = false; this.noLink = false; this.place = true; this.calls = [];
  },
};

/** A 2D context that records what was drawn. */
function recordingContext(): CanvasRenderingContext2D {
  const calls = stubControl.calls;
  return new Proxy({} as Record<string, unknown>, {
    get(target, key: string) {
      if (key in target) return target[key];
      if (key === 'createLinearGradient') return () => ({ addColorStop: () => undefined });
      return (...args: unknown[]) => { calls.push(`${key}(${args.map(String).join(',')})`); };
    },
    set(target, key: string, value) {
      target[key] = value;
      calls.push(`${key}=${String(value)}`);
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
}

const ForceGraph2D = React.forwardRef(function ForceGraph2DStub(props: AnyProps, ref: React.Ref<unknown>) {
  React.useImperativeHandle(ref, () => (stubControl.bare ? {} : {
    zoomToFit: () => undefined,
    centerAt: () => undefined,
    zoom: () => stubControl.zoom,
    d3ReheatSimulation: () => undefined,
    // GraphView sets its layout forces with d3Force(name, force) and reads the link force to tune
    // it. The getter calls the distance function on both kinds of link end, as the layout would.
    d3Force: (_name: string, force?: unknown) => {
      // A force is started on the nodes, as the simulation would, so its per-node functions run.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if (force !== undefined) { (force as any)?.initialize?.(props.graphData?.nodes ?? [], Math.random); return undefined; }
      if (stubControl.noLink) return undefined;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const chain: any = {
        distance: (fn: (l: unknown) => number) => {
          const [a, b] = props.graphData?.nodes ?? [];
          fn({ source: a ?? 'x', target: b ?? 'y' });
          fn({ source: 'x', target: 'y' });
          return chain;
        },
        strength: () => chain,
      };
      return chain;
    },
  }));
  const nodes: AnyProps[] = props.graphData?.nodes ?? [];
  const links: AnyProps[] = props.graphData?.links ?? [];
  const layout = () => {
    if (stubControl.place) nodes.forEach((n, i) => { n.x = i * 40 - 20; n.y = (i % 3) * 30 - 15; });
    const byId = new Map(nodes.map((n) => [n.id, n]));
    for (const l of links) {
      l.source = byId.get(l.source) ?? l.source;
      l.target = byId.get(l.target) ?? l.target;
    }
  };
  const paint = () => {
    const ctx = recordingContext();
    const s = stubControl.scale;
    for (const n of nodes) {
      props.nodeLabel?.(n); props.nodeVal?.(n); props.nodeColor?.(n); props.nodeCanvasObjectMode?.(n);
      props.nodeCanvasObject?.(n, ctx, s);
      props.nodePointerAreaPaint?.(n, '#f00', ctx, s);
    }
    for (const l of links) {
      props.linkColor?.(l); props.linkWidth?.(l); props.linkLineDash?.(l); props.linkLabel?.(l);
      props.linkCanvasObjectMode?.(l); props.linkDirectionalArrowColor?.(l);
      props.linkDirectionalArrowRelPos?.(l);
      props.linkCanvasObject?.(l, ctx, s);
    }
  };
  return (
    <div data-testid="force-graph-stub">
      <button data-testid="fg-layout" onClick={layout}>layout</button>
      <button data-testid="fg-paint" onClick={paint}>paint</button>
      <button data-testid="fg-background" onClick={() => props.onBackgroundClick?.()}>background</button>
      <button
        data-testid="fg-engine"
        onClick={() => {
          for (let i = 0; i < 32; i++) props.onEngineTick?.();
          props.onEngineStop?.();
        }}
      >
        engine
      </button>
      {nodes.map((n) => (
        <button
          key={n.id}
          data-testid={`fg-node-${n.id}`}
          onClick={() => props.onNodeClick?.(n)}
          onContextMenu={() => props.onNodeDragEnd?.(n)}
          onMouseEnter={() => props.onNodeHover?.(n)}
          onMouseLeave={() => props.onNodeHover?.(null)}
        >
          {n.title ?? n.id}
        </button>
      ))}
      {links.map((l) => (
        <button
          key={l.id}
          data-testid={`fg-link-${l.id}`}
          onClick={() => props.onLinkClick?.(l)}
          onMouseEnter={() => props.onLinkHover?.(l)}
          onMouseLeave={() => props.onLinkHover?.(null)}
        >
          {l.label ?? l.id}
        </button>
      ))}
    </div>
  );
});

export default ForceGraph2D;
