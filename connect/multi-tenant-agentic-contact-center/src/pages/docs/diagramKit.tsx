// Tiny layout kit shared by the docs diagrams (inline SVG, no dependencies).
//
// - Boxes are sized from their text (fitW) so titles never overflow.
// - Edges are orthogonal polylines through explicit waypoints, so they run in the
//   gaps between boxes instead of cutting across them.
// - Edge labels sit on a chosen segment with a white background.
// Every element carries data-kind / data-id so layout can be checked for overlaps.
import type { ReactNode } from "react";

export const ICON: Record<string, string> = {
  user: "/aws-icons/user.svg",
  cognito: "/aws-icons/cognito.svg",
  spa: "/aws-icons/client.svg",
  cloudfront: "/aws-icons/cloudfront.svg",
  apigw: "/aws-icons/apigateway.svg",
  lambda: "/aws-icons/lambda.svg",
  connect: "/aws-icons/connect.svg",
  amazonq: "/aws-icons/amazonq.svg",
  agentcore: "/aws-icons/agentcore.svg",
  opensearch: "/aws-icons/opensearch.svg",
  pipeline: "/aws-icons/opensearch-ingestion.svg",
  aurora: "/aws-icons/aurora.svg",
  attr: "/aws-icons/jsonscript.svg",
};

export const COLOR = {
  gray: "#475569",
  green: "#3f9142",
  blue: "#2E73B8",
  purple: "#8C4FFF",
};
export type EdgeColor = keyof typeof COLOR;

export interface Box {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  icon?: keyof typeof ICON;
  title: string;
  sub?: string;
  tone?: "default" | "green";
}

export interface Pt {
  x: number;
  y: number;
}

/** Font metrics used for layout (and by the card renderer). */
export interface Metrics {
  title: number;
  sub: number;
  tile: number;
}

// Conservative average glyph widths for system-ui (bold / regular).
export const textW = (s: string, size: number, bold = false) => s.length * size * (bold ? 0.62 : 0.56);

/** Width that fits the box's title and subtitle, never less than `min`. */
export function fitW(b: { title: string; sub?: string; icon?: string }, m: Metrics, min = 0) {
  const lead = b.icon ? m.tile + 22 : 16;
  const text = Math.max(textW(b.title, m.title, true), b.sub ? textW(b.sub, m.sub) : 0);
  return Math.ceil(Math.max(min, lead + text + 16));
}

// Anchors on a box side (offset along that side).
export const L = (b: Box, d = 0): Pt => ({ x: b.x - 2, y: b.y + b.h / 2 + d });
export const R = (b: Box, d = 0): Pt => ({ x: b.x + b.w + 2, y: b.y + b.h / 2 + d });
export const T = (b: Box, d = 0): Pt => ({ x: b.x + b.w / 2 + d, y: b.y - 2 });
export const B = (b: Box, d = 0): Pt => ({ x: b.x + b.w / 2 + d, y: b.y + b.h + 2 });

export interface Edge {
  from: string;
  to: string;
  pts: Pt[];
  label?: string;
  /** Segment index the label sits on (default: the longest segment). */
  seg?: number;
  /** Which side of the segment: above/below for horizontal, left/right for vertical. */
  side?: "above" | "below" | "left" | "right";
  color?: EdgeColor;
  dash?: boolean;
}

export function Frame({ vw, vh, aria, id, minWidth = 640, children }: {
  vw: number;
  vh: number;
  aria: string;
  id: string;
  minWidth?: number;
  children: ReactNode;
}) {
  return (
    <div className="not-prose my-6 overflow-x-auto rounded-xl border border-ink-200 bg-white p-3">
      <svg
        viewBox={`0 0 ${vw} ${vh}`}
        width="100%"
        role="img"
        aria-label={aria}
        data-diagram={id}
        style={{ display: "block", minWidth, height: "auto", fontFamily: "ui-sans-serif, system-ui, sans-serif" }}
      >
        <defs>
          {(Object.keys(COLOR) as EdgeColor[]).map((c) => (
            <marker
              key={c}
              id={`${id}-ah-${c}`}
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M0 0L10 5L0 10z" fill={COLOR[c]} />
            </marker>
          ))}
        </defs>
        {children}
      </svg>
    </div>
  );
}

export function Card({ b, m }: { b: Box; m: Metrics }) {
  const green = b.tone === "green";
  const tile = Math.min(m.tile, b.h - 14);
  const tx = b.icon ? b.x + tile + 20 : b.x + 14;
  const midY = b.y + b.h / 2;
  return (
    <g>
      <rect
        data-kind="card"
        data-id={b.id}
        x={b.x}
        y={b.y}
        width={b.w}
        height={b.h}
        rx="9"
        fill={green ? "#f4faf3" : "#ffffff"}
        stroke={green ? "#a9cfa6" : "#d7dee7"}
        strokeWidth="1.2"
      />
      {b.icon && (
        <image
          href={ICON[b.icon]}
          x={b.x + 10}
          y={midY - tile / 2}
          width={tile}
          height={tile}
          preserveAspectRatio="xMidYMid meet"
        />
      )}
      <text
        data-kind="card-text"
        data-id={b.id}
        x={tx}
        y={b.sub ? midY - 3 : midY + m.title * 0.35}
        fontSize={m.title}
        fontWeight={700}
        fill="#1f2937"
      >
        {b.title}
      </text>
      {b.sub && (
        <text data-kind="card-text" data-id={b.id} x={tx} y={midY + m.sub + 4} fontSize={m.sub} fill="#6b7280">
          {b.sub}
        </text>
      )}
    </g>
  );
}

export function EdgeLine({ e, frame }: { e: Edge; frame: string }) {
  const c = e.color ?? "gray";
  return (
    <polyline
      data-kind="edge"
      data-from={e.from}
      data-to={e.to}
      points={e.pts.map((p) => `${p.x},${p.y}`).join(" ")}
      fill="none"
      stroke={COLOR[c]}
      strokeWidth={e.dash ? 2 : 1.8}
      strokeDasharray={e.dash ? "6 4" : undefined}
      strokeLinejoin="round"
      markerEnd={`url(#${frame}-ah-${c})`}
    />
  );
}

const LABEL_SIZE = 9.5;

export function EdgeLabel({ e }: { e: Edge }) {
  if (!e.label) return null;
  const segs = e.pts.slice(1).map((p, i) => [e.pts[i], p] as const);
  const len = ([a, b]: readonly [Pt, Pt]) => Math.hypot(b.x - a.x, b.y - a.y);
  const idx = e.seg ?? segs.reduce((best, s, i) => (len(s) > len(segs[best]) ? i : best), 0);
  const [a, b] = segs[idx];
  const horizontal = Math.abs(b.y - a.y) < Math.abs(b.x - a.x);
  const w = textW(e.label, LABEL_SIZE) + 8;
  const h = LABEL_SIZE + 6;
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const side = e.side ?? (horizontal ? "above" : "right");
  let cx = mx;
  let cy = my;
  if (horizontal) cy = side === "below" ? my + h / 2 + 3 : my - h / 2 - 3;
  else cx = side === "left" ? mx - w / 2 - 5 : mx + w / 2 + 5;
  const c = COLOR[e.color ?? "gray"];
  return (
    <g data-kind="label">
      <rect x={cx - w / 2} y={cy - h / 2} width={w} height={h} rx="4" fill="#ffffff" opacity="0.92" />
      <text x={cx} y={cy + LABEL_SIZE * 0.35} fontSize={LABEL_SIZE} textAnchor="middle" fill={c}>
        {e.label}
      </text>
    </g>
  );
}

/** Edges under the cards, labels above them. */
export function Graph({ frame, boxes, edges, m }: { frame: string; boxes: Box[]; edges: Edge[]; m: Metrics }) {
  return (
    <>
      {edges.map((e, i) => (
        <EdgeLine key={`e${i}`} e={e} frame={frame} />
      ))}
      {boxes.map((b) => (
        <Card key={b.id} b={b} m={m} />
      ))}
      {edges.map((e, i) => (
        <EdgeLabel key={`l${i}`} e={e} />
      ))}
    </>
  );
}

export function Zone({ x, y, w, h, label, fill, stroke }: {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  fill: string;
  stroke: string;
}) {
  return (
    <g>
      <rect data-kind="zone" x={x} y={y} width={w} height={h} rx="12" fill={fill} stroke={stroke} strokeWidth="1" />
      <text data-kind="zone-label" x={x + 12} y={y + 19} fontSize="11.5" fontWeight={700} fill="#64748b">
        {label}
      </text>
    </g>
  );
}

export function Lane({ x, y, text }: { x: number; y: number; text: string }) {
  return (
    <text data-kind="lane" x={x} y={y} fontSize="11.5" fontWeight={700} fill="#64748b">
      {text}
    </text>
  );
}

export function Note({ x, y, w, lines, tone = "gray", heading = false }: {
  x: number;
  y: number;
  w: number;
  lines: string[];
  tone?: "green" | "gray";
  heading?: boolean;
}) {
  const h = 18 + lines.length * 16;
  return (
    <g>
      <rect
        data-kind="note"
        x={x}
        y={y}
        width={w}
        height={h}
        rx="8"
        fill={tone === "green" ? "#eef7ee" : "#f1f5f9"}
        stroke={tone === "green" ? "#a9cfa6" : "#cbd5e1"}
        strokeWidth="1"
      />
      {lines.map((ln, i) => (
        <text
          key={i}
          data-kind="note-text"
          x={x + 14}
          y={y + 21 + i * 16}
          fontSize="10.5"
          fontWeight={heading && i === 0 ? 700 : 400}
          fill="#374151"
        >
          {ln}
        </text>
      ))}
    </g>
  );
}
export const noteH = (lines: number) => 18 + lines * 16;
