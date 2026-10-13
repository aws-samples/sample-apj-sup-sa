// Solution architecture diagram for the developer docs.
//
// Self-contained, responsive inline SVG that mirrors the draw.io "AWS icons"
// architecture (docs/anycompany-pay-architecture-full-aws-icons.drawio): both use cases
// — (A) support cases and (B) live-chat agentic self-service — sharing Cognito,
// the API layer and one Amazon Connect instance, with the trusted-merchant_id
// tenant-isolation path highlighted. Boxes are sized from their text and edges are
// routed through the gaps (see diagramKit).
import { B, COLOR, Frame, Graph, L, R, T, Zone, fitW, type Box, type Edge, type EdgeColor, type Metrics } from "./diagramKit";

const M: Metrics = { title: 12.5, sub: 9.5, tile: 30 };
const H = 54;
const GAP = 52;
const ROW = [94, 156, 236, 322, 408]; // r0 (users chip) .. r4

type Spec = Omit<Box, "x" | "y" | "w" | "h"> & { row: number; h?: number };

// Columns, left to right; each box sits in a row.
const COLS: { zone: string; fill: string; stroke: string; gapAfter: number; boxes: Spec[] }[] = [
  {
    zone: "Client",
    fill: "#eef4fb",
    stroke: "#c3d4e8",
    gapAfter: GAP,
    boxes: [
      { id: "users", row: 0, h: 40, icon: "user", title: "Merchant / Agent" },
      { id: "cognito", row: 1, icon: "cognito", title: "Amazon Cognito", sub: "ID token · merchant_id" },
      { id: "spa", row: 2, icon: "spa", title: "AnyCompanyPay SPA", sub: "React app" },
    ],
  },
  {
    zone: "Edge + API",
    fill: "#fdf0e6",
    stroke: "#e8c39b",
    gapAfter: GAP,
    boxes: [
      { id: "cloudfront", row: 2, icon: "cloudfront", title: "CloudFront + ECS", sub: "serves the SPA" },
      { id: "apigw", row: 3, icon: "apigw", title: "API Gateway", sub: "Cognito JWT authorizer" },
    ],
  },
  {
    zone: "Lambda (API)",
    fill: "#fdf0e6",
    stroke: "#e8c39b",
    gapAfter: GAP,
    boxes: [
      { id: "casesFn", row: 2, icon: "lambda", title: "CasesApiFn", sub: "cases CRUD" },
      { id: "chatFn", row: 3, icon: "lambda", title: "ChatApiFn", sub: "POST /chat/start" },
    ],
  },
  {
    zone: "Amazon Connect",
    fill: "#fff7e0",
    stroke: "#e4cf8f",
    gapAfter: 80,
    boxes: [
      { id: "cases", row: 2, icon: "connect", title: "Connect Cases", sub: "support cases" },
      { id: "connect", row: 3, icon: "connect", title: "Amazon Connect", sub: "chat flow · Lex" },
      { id: "attrs", row: 4, icon: "attr", title: "Contact attributes", sub: "merchant_id · case_id" },
    ],
  },
  {
    zone: "Q in Connect · AgentCore",
    fill: "#efe9f6",
    stroke: "#c9b6e0",
    gapAfter: 0,
    boxes: [
      { id: "qic", row: 3, icon: "amazonq", title: "Q in Connect", sub: "orchestrator agent" },
      { id: "gateway", row: 4, icon: "agentcore", title: "AgentCore Gateway", sub: "MCP · interceptor" },
    ],
  },
];

function layout() {
  const boxes: Record<string, Box> = {};
  const zones: { x: number; y: number; w: number; h: number; label: string; fill: string; stroke: string }[] = [];
  let x = 36;
  const top = 64;
  const bottom = ROW[4] + H + 18;
  COLS.forEach((c, i) => {
    const w = Math.max(...c.boxes.map((s) => fitW(s, M)));
    for (const s of c.boxes) boxes[s.id] = { ...s, x, y: ROW[s.row], w, h: s.h ?? H };
    const zTop = i === COLS.length - 1 ? ROW[3] - 38 : top;
    zones.push({ x: x - 16, y: zTop, w: w + 32, h: bottom - zTop, label: c.zone, fill: c.fill, stroke: c.stroke });
    x += w + c.gapAfter;
  });
  // Data plane (bottom band): Aurora -> zero-ETL -> OpenSearch <- tool, the tool under the gateway.
  const dataY = 578;
  const gw = boxes.gateway;
  const tool: Box = { id: "tool", icon: "lambda", title: "Transaction tool", sub: "trusted tenant only", x: gw.x, y: dataY, w: 0, h: H };
  tool.w = fitW(tool, M);
  const os: Box = { id: "opensearch", icon: "opensearch", title: "OpenSearch Serverless", sub: "index: transactions", x: 0, y: dataY, w: 0, h: H };
  os.w = fitW(os, M);
  os.x = tool.x - 120 - os.w;
  const etl: Box = { id: "zeroetl", icon: "pipeline", title: "Zero-ETL (OSIS)", sub: "OpenSearch Ingestion", x: 0, y: dataY, w: 0, h: H };
  etl.w = fitW(etl, M);
  etl.x = os.x - 88 - etl.w;
  const aurora: Box = { id: "aurora", icon: "aurora", title: "Aurora PostgreSQL", sub: "system of record", x: 0, y: dataY, w: 0, h: H };
  aurora.w = fitW(aurora, M);
  aurora.x = etl.x - 100 - aurora.w;
  for (const b of [tool, os, etl, aurora]) boxes[b.id] = b;
  zones.push({
    x: aurora.x - 20,
    y: dataY - 30,
    w: tool.x + tool.w + 20 - (aurora.x - 20),
    h: H + 48,
    label: "Aurora VPC — private data plane",
    fill: "#e7f3e6",
    stroke: "#a9cfa6",
  });
  const width = Math.max(x, tool.x + tool.w) + 36;
  return { boxes, zones, width };
}

const { boxes: N, zones: ZONES, width: VW } = layout();
const VH = 724;
const gapMid = (a: Box, b: Box) => (a.x + a.w + b.x) / 2;

const EDGES: Edge[] = [
  { from: "cognito", to: "spa", pts: [B(N.cognito), T(N.spa)], label: "login" },
  { from: "cloudfront", to: "spa", pts: [L(N.cloudfront), R(N.spa)], label: "serves" },
  {
    from: "spa",
    to: "apigw",
    pts: [B(N.spa), { x: B(N.spa).x, y: L(N.apigw).y }, L(N.apigw)],
    seg: 1,
    label: "REST · Bearer ID token",
  },
  // A — support cases
  {
    from: "apigw",
    to: "casesFn",
    pts: [R(N.apigw, -10), { x: gapMid(N.apigw, N.casesFn), y: R(N.apigw, -10).y }, { x: gapMid(N.apigw, N.casesFn), y: L(N.casesFn).y }, L(N.casesFn)],
    seg: 1,
    side: "left",
    label: "cases",
    color: "blue",
  },
  { from: "casesFn", to: "cases", pts: [R(N.casesFn), L(N.cases)], label: "CRUD", color: "blue" },
  // B — chat / agentic
  { from: "apigw", to: "chatFn", pts: [R(N.apigw, 10), L(N.chatFn, 10)] },
  { from: "chatFn", to: "connect", pts: [R(N.chatFn), L(N.connect)] },
  {
    from: "chatFn",
    to: "attrs",
    pts: [B(N.chatFn), { x: B(N.chatFn).x, y: L(N.attrs).y }, L(N.attrs)],
    seg: 1,
    label: "stamp merchant_id",
    color: "green",
    dash: true,
  },
  { from: "connect", to: "qic", pts: [R(N.connect), L(N.qic)], label: "Lex" },
  { from: "qic", to: "gateway", pts: [B(N.qic), T(N.gateway)], label: "tools/call" },
  { from: "gateway", to: "attrs", pts: [L(N.gateway), R(N.attrs)], label: "read tenant", color: "green", dash: true },
  {
    from: "gateway",
    to: "tool",
    pts: [B(N.gateway), { x: B(N.gateway).x, y: N.tool.y - 2 }],
    side: "left",
    label: "inject __trusted_merchant_id",
    color: "green",
    dash: true,
  },
  { from: "tool", to: "opensearch", pts: [L(N.tool), R(N.opensearch)], label: "filter merchant_id", color: "green", dash: true },
  // Data plane — Aurora is the system of record; zero-ETL keeps OpenSearch in sync
  { from: "aurora", to: "zeroetl", pts: [R(N.aurora), L(N.zeroetl)], label: "WAL + snapshot", color: "purple" },
  { from: "zeroetl", to: "opensearch", pts: [R(N.zeroetl), L(N.opensearch)], label: "index docs", color: "purple" },
];

const LEGEND: { label: string; color: EdgeColor; dash?: boolean }[] = [
  { label: "Request / serve", color: "gray" },
  { label: "Support cases (Connect Cases)", color: "blue" },
  { label: "Zero-ETL sync", color: "purple" },
  { label: "Trusted merchant_id — tenant isolation", color: "green", dash: true },
];

export function ArchitectureDiagram() {
  return (
    <Frame
      id="arch"
      vw={VW}
      vh={VH}
      minWidth={760}
      aria="AnyCompanyPay solution architecture: support cases and live-chat agentic self-service on Amazon Connect and Bedrock AgentCore, tenant-isolated by merchant_id."
    >
      {ZONES.map((z) => (
        <Zone key={z.label} {...z} />
      ))}
      <Graph frame="arch" boxes={Object.values(N)} edges={EDGES} m={M} />
      <rect data-kind="note" x={36} y={VH - 50} width={900} height={34} rx="8" fill="#ffffff" stroke="#e5e7eb" />
      {LEGEND.map((l, i) => {
        const lx = 50 + [0, 160, 400, 560][i];
        const ly = VH - 33;
        return (
          <g key={l.label}>
            <line x1={lx} y1={ly} x2={lx + 24} y2={ly} stroke={COLOR[l.color]} strokeWidth="2.4" strokeDasharray={l.dash ? "6 4" : undefined} />
            <text x={lx + 32} y={ly + 3.5} fontSize="10.5" fill="#374151">
              {l.label}
            </text>
          </g>
        );
      })}
    </Frame>
  );
}
