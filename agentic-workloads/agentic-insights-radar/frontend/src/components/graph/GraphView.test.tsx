// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { stubControl } from '@/test/forceGraphStub';

import {
  edgeFamilyColor,
  edgeFamilyKey,
  GraphView,
  humanizeEdge,
  legendDisplayName,
  nodeColor,
  plainText,
  tooltipPlacement,
  firstText,
  type GraphEdge,
  type GraphNode,
} from './GraphView';

const n = (id: string, label: string, over: Partial<GraphNode> = {}): GraphNode => ({
  id, label, properties: { title: `T ${id}` }, ...over,
});
const e = (from: string, label: string, to: string): GraphEdge => ({ id: `${from}|${label}|${to}`, from, to, label, properties: {} });

const NODES: GraphNode[] = [
  n('a', 'organization', { degree: 9, hiddenNeighbourCount: 3, properties: { title: 'A title that is well over thirty-six characters long', summary: '<b>Hub</b> body', evidence_url: 'https://x.test/a' } }),
  n('b', 'fact', { hiddenNeighbourCount: 120 }),
  n('c', 'insight:regulatory_change', { properties: { title: ' ' } }),
  n('d', 'signal', { properties: { title: 'D', description: 'Desc', summary: 'word '.repeat(40) } }),
  n('t', 'topic', { properties: { title: 'Topic', description: '', summary: 'Topic summary', source: ' ', provenance: 'scout' } }),
];
const EDGES: GraphEdge[] = [
  e('a', 'about', 'b'),
  e('b', 'contradicts', 'a'),
  e('c', '', 'a'),
  e('d', 'SUPPORTS', 'a'),
];

/** Every paint call, as text: enough to tell which branches drew. */
const painted = () => stubControl.calls.join('\n');
const paintAt = (scale: number) => {
  stubControl.calls = [];
  stubControl.scale = scale;
  fireEvent.click(screen.getByTestId('fg-paint'));
  return painted();
};

/** A matchMedia whose listeners can be fired, with `matches` set per query. */
function mockMatchMedia(matches: (q: string) => boolean) {
  const listeners: (() => void)[] = [];
  window.matchMedia = ((q: string) => ({
    matches: matches(q),
    addEventListener: (_: string, fn: () => void) => listeners.push(fn),
    removeEventListener: () => undefined,
  })) as unknown as typeof window.matchMedia;
  return () => act(() => listeners.forEach((fn) => fn()));
}

beforeEach(() => stubControl.reset());
afterEach(() => {
  vi.useRealTimers();
  // @ts-expect-error jsdom has no matchMedia: put it back the way it was.
  delete window.matchMedia;
  document.body.classList.remove('awsui-dark-mode');
  vi.unstubAllGlobals();
});

describe('helpers', () => {
  it('colours, names and places', () => {
    expect(nodeColor('signal')).toBe('#e6550d');
    expect(nodeColor('insight:x')).toBe(nodeColor('insight:x'));
    expect(legendDisplayName('insight:regulatory_change')).toBe(legendDisplayName('regulatory_change'));
    expect(legendDisplayName('insight:k', { 'insight:k': 'Watch' })).toBe('Watch');
    expect(plainText('<p>a **b**</p>  c')).toBe('a b c');
    expect(tooltipPlacement(10, 10, 800, 600)).toEqual({ left: 26, top: 26 });
    expect(firstText('', '  ', 3, 'x', 'y')).toBe('x');
    expect(firstText(undefined, null)).toBeUndefined();
    expect(tooltipPlacement(790, 590, 800, 600)).toEqual({ left: 494, top: 434 });
    expect(edgeFamilyKey('')).toBe('related');
    expect(edgeFamilyKey('COMPETES_WITH')).toBe('competes_with');
    expect(edgeFamilyColor('supports')).toBe('#2ca02c');
    expect(humanizeEdge('')).toBe('');
  });
});

describe('GraphView', () => {
  it('draws nothing but an empty list and no legend without nodes', () => {
    render(<GraphView nodes={[]} edges={[]} />);
    expect(screen.getByText('The graph as a list: 0 nodes and 0 links')).toBeInTheDocument();
    expect(screen.queryByTestId('graph-legend')).toBeNull();
  });

  it('paints the overview level by level as the user zooms in', () => {
    vi.useFakeTimers();
    render(<GraphView nodes={NODES} edges={EDGES} />);
    act(() => { vi.advanceTimersByTime(2000); }); // fit, settle, capture
    // Before the layout: link ends are still ids, so no edge label is drawn and arrows sit at 0.96.
    expect(paintAt(4)).not.toContain('strokeText');
    fireEvent.click(screen.getByTestId('fg-layout'));
    expect(paintAt(1)).toContain('fillText(A title that is well over thirty-six\u2026');
    expect(painted()).not.toContain('fillText(+3');
    expect(paintAt(2)).toContain('fillText(+3');
    expect(painted()).toContain('fillText(99+');
    expect(paintAt(3)).toContain('fillText(c,'); // a blank title falls back to the id
    const zoomed = paintAt(4);
    expect(zoomed).toContain('strokeText(about');
    expect(zoomed).toContain('strokeText(supports');
    expect(zoomed).not.toContain('strokeText(,'); // a nameless link has no label
    // The layout is settled now: painting pins every node where it is.
    fireEvent.click(screen.getByTestId('fg-engine'));
    paintAt(1);
    fireEvent.contextMenu(screen.getByTestId('fg-node-a')); // the end of a drag pins the node too
  });

  it('paints the one-node view relative to the zoom it was fitted at', () => {
    vi.useFakeTimers();
    stubControl.zoom = 2;
    const { rerender } = render(<GraphView nodes={NODES} edges={EDGES} mode="scoped" focusId="a" />);
    fireEvent.click(screen.getByTestId('fg-layout'));
    // Before the fit: only the top nodes are labelled, and links carry no names.
    expect(paintAt(10)).not.toContain('strokeText');
    expect(painted()).toContain('fillText(+3'); // badges always show here
    act(() => { vi.advanceTimersByTime(2000); });
    expect(paintAt(2.5)).not.toContain('strokeText');
    expect(paintAt(3)).toContain('fillText(c,');
    expect(paintAt(5)).toContain('strokeText(about');
    // The selected and the hovered node in this view.
    rerender(<GraphView nodes={NODES} edges={EDGES} mode="scoped" focusId="a" selectedId="a" />);
    fireEvent.mouseEnter(screen.getByTestId('fg-node-b'));
    expect(paintAt(1)).toContain('fillText(A title that is well over thirty-six\u2026');
    // A zoom that is not a number leaves the base where it was.
    stubControl.zoom = 0;
    rerender(<GraphView nodes={NODES.slice(0, 3)} edges={EDGES} mode="scoped" focusId="a" />);
    act(() => { vi.advanceTimersByTime(2000); });
  });

  it('dims everything outside the selection and names the hovered parts', () => {
    render(<GraphView nodes={NODES} edges={EDGES} selectedId="b" />);
    fireEvent.click(screen.getByTestId('fg-layout'));
    fireEvent.mouseEnter(screen.getByTestId('fg-node-b'));
    fireEvent.mouseEnter(screen.getByTestId('fg-link-a|about|b'));
    const sel = paintAt(4);
    expect(sel).toContain('globalAlpha=0.12'); // c and d are not next to b
    expect(sel).toContain(`strokeStyle=${'#ec7211'}`);
    expect(sel).toContain('fillStyle=#232f3e');
    fireEvent.mouseEnter(screen.getByTestId('fg-node-a')); // hovered, not selected
    expect(paintAt(1)).toContain('strokeStyle=#232f3e');
    fireEvent.mouseLeave(screen.getByTestId('fg-link-a|about|b'));
    fireEvent.mouseLeave(screen.getByTestId('fg-node-a'));
  });

  it('shows a tooltip by the pointer, with or without a body and a source', () => {
    render(<GraphView nodes={NODES} edges={EDGES} names={{ 'insight:regulatory_change': 'Regulation' }} />);
    fireEvent.mouseEnter(screen.getByTestId('fg-node-a'));
    const tip = screen.getByTestId('graph-tooltip');
    expect(tip).toHaveTextContent('Hub body');
    expect(tip).toHaveTextContent('https://x.test/a');
    expect(tip.style.left).toBe('12px');
    fireEvent.mouseMove(screen.getByTestId('graph-view'), { clientX: 100, clientY: 50 });
    expect(screen.getByTestId('graph-tooltip').style.left).toBe('116px');
    fireEvent.mouseEnter(screen.getByTestId('fg-node-c'));
    expect(screen.getByTestId('graph-tooltip')).toHaveTextContent('Regulation');
    fireEvent.mouseEnter(screen.getByTestId('fg-node-d'));
    expect(screen.getByTestId('graph-tooltip')).toHaveTextContent('Desc');
    // An empty description (topics and organisations keep their text in summary) is not shown as blank.
    fireEvent.mouseEnter(screen.getByTestId('fg-node-t'));
    expect(screen.getByTestId('graph-tooltip')).toHaveTextContent('Topic summary');
    expect(screen.getByTestId('graph-tooltip')).toHaveTextContent('scout');
    fireEvent.mouseEnter(screen.getByTestId('fg-node-d'));
    fireEvent.mouseLeave(screen.getByTestId('graph-view'));
    // Hiding the hovered node's type leaves no tooltip for it.
    fireEvent.click(screen.getByTestId('legend-signal'));
    expect(screen.queryByTestId('graph-tooltip')).toBeNull();
  });

  it('tells a click from a double-click on the canvas', () => {
    vi.useFakeTimers();
    const onNodeClick = vi.fn();
    const onNodeDoubleClick = vi.fn();
    const onEdgeClick = vi.fn();
    const { unmount } = render(
      <GraphView nodes={NODES} edges={EDGES} onNodeClick={onNodeClick} onNodeDoubleClick={onNodeDoubleClick} onEdgeClick={onEdgeClick} />,
    );
    fireEvent.click(screen.getByTestId('fg-node-a'));
    fireEvent.click(screen.getByTestId('fg-node-a'));
    expect(onNodeDoubleClick).toHaveBeenCalledWith(NODES[0]);
    fireEvent.click(screen.getByTestId('fg-node-a'));
    fireEvent.click(screen.getByTestId('fg-node-b')); // another node restarts the wait
    act(() => { vi.advanceTimersByTime(300); });
    expect(onNodeClick).toHaveBeenCalledTimes(1);
    expect(onNodeClick).toHaveBeenCalledWith(NODES[1]);
    fireEvent.click(screen.getByTestId('fg-link-a|about|b'));
    expect(onEdgeClick).toHaveBeenCalledWith(EDGES[0]);
    fireEvent.click(screen.getByTestId('fg-background'));
    fireEvent.click(screen.getByTestId('fg-node-c'));
    unmount(); // a click still waiting is dropped
  });

  it('works without any handlers', () => {
    render(<GraphView nodes={NODES} edges={EDGES} />);
    fireEvent.click(screen.getByTestId('graph-node-c'));
    fireEvent.doubleClick(screen.getByTestId('graph-node-a'));
    fireEvent.click(screen.getByTestId('graph-edge-a|about|b'));
    expect(paintAt(1)).toContain('globalAlpha=0.12'); // its own selection dims the rest
  });

  it('filters by type from the legend, but never hides the focus node', () => {
    render(<GraphView nodes={NODES} edges={EDGES} mode="scoped" focusId="a" />);
    expect(screen.getByTestId('legend-fact')).toHaveTextContent('Fact (evidence)');
    fireEvent.click(screen.getByTestId('legend-organization'));
    fireEvent.click(screen.getByTestId('legend-fact'));
    expect(screen.getByTestId('legend-fact')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('graph-node-a')).toBeInTheDocument();
    expect(screen.queryByTestId('graph-node-b')).toBeNull();
    fireEvent.click(screen.getByTestId('legend-fact'));
    expect(screen.getByTestId('graph-node-b')).toBeInTheDocument();
    expect(screen.getByTestId('graph-edge-legend')).toBeInTheDocument();
  });

  it('has no relationship legend without links', () => {
    render(<GraphView nodes={NODES} edges={[]} />);
    expect(screen.queryByTestId('graph-edge-legend')).toBeNull();
  });

  it('lays out without a handle, or without a link force', () => {
    stubControl.bare = true;
    const first = render(<GraphView nodes={NODES} edges={EDGES} />);
    first.unmount();
    stubControl.bare = false;
    stubControl.noLink = true;
    render(<GraphView nodes={NODES} edges={EDGES} />).unmount();
    render(<GraphView nodes={NODES} edges={EDGES} mode="scoped" />).unmount();
    stubControl.noLink = false;
    render(<GraphView nodes={[]} edges={[]} mode="scoped" />).unmount();
  });

  it('places arrows clear of the target, whatever the layout', () => {
    const many = Array.from({ length: 130 }, (_, i) => n(`m${i}`, 'topic'));
    render(<GraphView nodes={[...many, n('z', 'topic')]} edges={[e('m0', 'about', 'm1'), e('z', 'about', 'z')]} mode="scoped" />);
    fireEvent.click(screen.getByTestId('fg-layout'));
    paintAt(1);
  });

  it('labels a link even before its ends have a position', () => {
    stubControl.place = false;
    render(<GraphView nodes={NODES} edges={EDGES} />);
    fireEvent.click(screen.getByTestId('fg-layout'));
    expect(paintAt(4)).toContain('translate(0,0)');
  });

  it('spaces a large overview', () => {
    const many = Array.from({ length: 130 }, (_, i) => n(`m${i}`, 'topic'));
    render(<GraphView nodes={many} edges={[]} />);
    expect(screen.getByText('The graph as a list: 130 nodes and 0 links')).toBeInTheDocument();
  });

  it('spaces a mid-size overview', () => {
    const many = Array.from({ length: 50 }, (_, i) => n(`m${i}`, 'topic'));
    render(<GraphView nodes={many} edges={[]} />);
    expect(screen.getAllByRole('button', { name: /^Topic:/ })).toHaveLength(50);
  });

  it('follows the theme and the screen', () => {
    document.body.classList.add('awsui-dark-mode');
    const dark = render(<GraphView nodes={NODES} edges={EDGES} />);
    expect(paintAt(1)).toContain('fillStyle=#e9ebed');
    dark.unmount();
    document.body.classList.remove('awsui-dark-mode');
    let prefersDark = true;
    const fire = mockMatchMedia((q) => q.includes('dark') && prefersDark);
    const observed: Element[] = [];
    vi.stubGlobal('ResizeObserver', class {
      constructor(private cb: () => void) {}
      observe(el: Element) { observed.push(el); this.cb(); }
      disconnect() { /* nothing to undo */ }
    });
    const live = render(<GraphView nodes={NODES} edges={EDGES} />);
    expect(paintAt(1)).toContain('fillStyle=#e9ebed');
    prefersDark = false;
    fire();
    expect(paintAt(1)).toContain('fillStyle=#1a1a1a');
    expect(observed).toHaveLength(1);
    live.unmount();
  });

  it('follows the window when there is no ResizeObserver', () => {
    const { unmount } = render(<GraphView nodes={NODES} edges={EDGES} />);
    act(() => { window.dispatchEvent(new Event('resize')); });
    unmount();
  });
});
