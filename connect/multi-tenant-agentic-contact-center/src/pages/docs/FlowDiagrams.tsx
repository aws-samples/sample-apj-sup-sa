// Flow diagrams for the developer docs "Architecture" deep-dive subsections:
//   - AgentCoreFlow  : how the Bedrock AgentCore Gateway invocation works
//   - ConnectFlow    : the Amazon Connect chat / contact-flow routing
//   - IsolationFlow  : end-to-end tenant isolation (login -> database)
//
// Self-contained, responsive inline SVG (viewBox scales to container width),
// reusing the official AWS icons under /public/aws-icons. Boxes are sized from
// their text and edges are routed through the gaps (see diagramKit).
import { B, COLOR, Frame, Graph, L, Lane, Note, R, T, fitW, noteH, type Box, type Edge, type Metrics } from "./diagramKit";

const M: Metrics = { title: 12, sub: 9.5, tile: 26 };
const H = 58;

type Spec = Omit<Box, "w"> & { w?: number };
const box = (s: Spec): Box => ({ ...s, w: s.w ?? fitW(s, M) });

/** Place boxes left to right from x with a fixed gap; returns them in order. */
function row(x: number, y: number, gap: number, specs: Omit<Spec, "x" | "y">[]): Box[] {
  return specs.map((s) => {
    const b = box({ ...s, x, y, h: s.h ?? H });
    x += b.w + gap;
    return b;
  });
}
const byId = (bs: Box[]) => Object.fromEntries(bs.map((b) => [b.id, b])) as Record<string, Box>;

// ============================================================================
// 1) AgentCore invocation flow — a snake: top row left->right, then down at the
//    gateway into the tenant-enforcing hops, bottom row right->left.
// ============================================================================
export function AgentCoreFlow() {
  const gap = 72;
  const top = row(16, 24, gap, [
    { id: "merchant", h: H, icon: "user", title: "Merchant", sub: "asks in live chat" },
    { id: "connect", h: H, icon: "connect", title: "Amazon Connect", sub: "chat flow · Lex" },
    { id: "qic", h: H, icon: "amazonq", title: "Q in Connect", sub: "orchestrator agent" },
    { id: "gateway", h: H, icon: "agentcore", title: "AgentCore Gateway", sub: "MCP · CUSTOM_JWT" },
  ]);
  const t = byId(top);
  // Bottom row, right-aligned under the gateway: interceptor <- tool <- OpenSearch reads right to left.
  const y2 = 152;
  const interceptor = box({ id: "interceptor", x: 0, y: y2, h: H, icon: "lambda", title: "Interceptor", sub: "tenant gate", tone: "green" });
  interceptor.x = t.gateway.x + t.gateway.w - interceptor.w;
  const tool = box({ id: "tool", x: 0, y: y2, h: H, icon: "lambda", title: "Transaction tool", sub: "MCP target", tone: "green" });
  tool.x = interceptor.x - gap - tool.w;
  const search = box({ id: "search", x: 0, y: y2, h: H, icon: "opensearch", title: "OpenSearch", sub: "private collection" });
  search.x = tool.x - gap - search.w;
  const boxes = [...top, interceptor, tool, search];
  const edges: Edge[] = [
    { from: "merchant", to: "connect", pts: [R(t.merchant), L(t.connect)] },
    { from: "connect", to: "qic", pts: [R(t.connect), L(t.qic)] },
    { from: "qic", to: "gateway", pts: [R(t.qic), L(t.gateway)], label: "tools/call" },
    {
      from: "gateway",
      to: "interceptor",
      pts: [B(t.gateway, interceptor.x + interceptor.w / 2 - (t.gateway.x + t.gateway.w / 2)), T(interceptor)],
      label: "every request",
      color: "green",
    },
    { from: "interceptor", to: "tool", pts: [L(interceptor), R(tool)], color: "green" },
    { from: "tool", to: "search", pts: [L(tool), R(search)], label: "filter", color: "green" },
  ];
  const vw = t.gateway.x + t.gateway.w + 16;
  const noteLines = [
    "Request interceptor — runs on every tools/call (never trusts the model):",
    "1 · strips any model-supplied merchant_id / merchant / tenant keys",
    "2 · GetContactAttributes(contactId) → resolves the trusted merchant_id",
    "3 · injects __trusted_merchant_id — the only key the tool will read (else it fails closed)",
  ];
  const noteY = y2 + H + 28;
  return (
    <Frame
      id="agentcore"
      vw={vw}
      vh={noteY + noteH(noteLines.length) + 12}
      aria="AgentCore invocation flow: merchant chat to Amazon Connect to Q in Connect orchestrator to the AgentCore Gateway, whose request interceptor injects the trusted tenant before the transaction tool queries OpenSearch."
    >
      <Graph frame="agentcore" boxes={boxes} edges={edges} m={M} />
      <Note x={16} y={noteY} w={vw - 32} tone="green" heading lines={noteLines} />
    </Frame>
  );
}

// ============================================================================
// 2) Amazon Connect chat / contact-flow routing
// ============================================================================
export function ConnectFlow() {
  const gap = 56;
  const spineY = 168;
  const spine = byId(
    row(16, spineY, gap, [
      { id: "merchant", h: H, icon: "user", title: "Merchant", sub: "SPA live chat" },
      { id: "chat", h: H, icon: "lambda", title: "ChatApiFn", sub: "StartChatContact" },
      { id: "flow", h: H, icon: "connect", title: "Contact flow", sub: "inbound (or routed) chat flow" },
    ])
  );
  const fx = spine.flow.x + 34; // branch connector x, on the flow box's left part
  const bx = fx + 72;
  const topY = 64;
  const botY = 272;
  const agentic = byId(
    row(bx, topY, gap, [
      { id: "lex", h: H, title: "Amazon Lex", sub: "Get input · QInConnectIntent" },
      { id: "qic", h: H, icon: "amazonq", title: "Q in Connect", sub: "self-service orchestrator" },
      { id: "reply", h: H, title: "Reply to merchant", sub: "multi-turn" },
    ])
  );
  const human = byId(
    row(bx, botY, gap, [
      { id: "queue", h: H, icon: "connect", title: "Queue + transfer", sub: "set queue → transfer" },
      { id: "routing", h: H, icon: "connect", title: "Routing profile", sub: "anycompany-pay-chat (CHAT)" },
      { id: "agent", h: H, icon: "user", title: "Human agent", sub: "CCP (Streams)" },
    ])
  );
  const edges: Edge[] = [
    { from: "merchant", to: "chat", pts: [R(spine.merchant), L(spine.chat)] },
    { from: "chat", to: "flow", pts: [R(spine.chat), L(spine.flow)] },
    {
      from: "flow",
      to: "lex",
      pts: [{ x: fx, y: spine.flow.y - 2 }, { x: fx, y: L(agentic.lex).y }, L(agentic.lex)],
      seg: 0,
      side: "left",
      label: "self-service",
      color: "green",
    },
    {
      from: "flow",
      to: "queue",
      pts: [{ x: fx, y: spine.flow.y + H + 2 }, { x: fx, y: L(human.queue).y }, L(human.queue)],
      seg: 0,
      side: "left",
      label: "Escalate",
    },
    { from: "lex", to: "qic", pts: [R(agentic.lex), L(agentic.qic)] },
    { from: "qic", to: "reply", pts: [R(agentic.qic), L(agentic.reply)] },
    { from: "queue", to: "routing", pts: [R(human.queue), L(human.routing)] },
    { from: "routing", to: "agent", pts: [R(human.routing), L(human.agent)] },
  ];
  const boxes = [...Object.values(spine), ...Object.values(agentic), ...Object.values(human)];
  const vw = human.agent.x + human.agent.w + 16;
  const loopY = 22;
  const rc = agentic.reply.x + agentic.reply.w / 2;
  const lc = agentic.lex.x + agentic.lex.w / 2;
  const noteLines = [
    "Chats started from a case use the case flow (stamps case_id after verifying tenant ownership); when the chat ends,",
    "the transcript is saved back to the case as a Connect Cases comment. With connect-routing, chats use the routed flow.",
  ];
  const noteY = botY + H + 28;
  return (
    <Frame
      id="connectflow"
      vw={vw}
      vh={noteY + noteH(noteLines.length) + 12}
      aria="Amazon Connect chat routing: ChatApiFn starts the contact with JWT-sourced attributes; the contact flow branches to the Q in Connect self-service orchestrator (via Lex) or transfers to a human agent through the support queue and routing profile."
    >
      <Lane x={16} y={topY + 20} text="Agentic self-service (multi-turn)" />
      <Lane x={16} y={botY + 34} text="Human handoff" />
      {/* multi-turn loop: reply back to Lex, above the agentic row */}
      <polyline
        data-kind="edge"
        data-from="reply"
        data-to="lex"
        points={`${rc},${topY - 2} ${rc},${loopY} ${lc},${loopY} ${lc},${topY - 2}`}
        fill="none"
        stroke={COLOR.green}
        strokeWidth="1.6"
        strokeDasharray="5 4"
        markerEnd="url(#connectflow-ah-green)"
      />
      <text data-kind="label" x={(rc + lc) / 2} y={loopY - 6} fontSize="9.5" textAnchor="middle" fill={COLOR.green}>
        continue conversation
      </text>
      <Graph frame="connectflow" boxes={boxes} edges={edges} m={M} />
      <Note x={16} y={noteY} w={vw - 32} lines={noteLines} />
    </Frame>
  );
}

// ============================================================================
// 3) End-to-end tenant isolation (login -> database)
// ============================================================================
export function IsolationFlow() {
  const gap = 56;
  const front = byId(
    row(16, 28, gap, [
      { id: "merchant", h: H, icon: "user", title: "Merchant", sub: "signs in" },
      { id: "cognito", h: H, icon: "cognito", title: "Amazon Cognito", sub: "ID token · merchant_id" },
      { id: "spa", h: H, icon: "spa", title: "AnyCompanyPay SPA", sub: "Bearer ID token" },
      { id: "apigw", h: H, icon: "apigw", title: "API Gateway", sub: "Cognito JWT authorizer" },
    ])
  );
  // Path B (agentic): ChatApiFn stamps the tenant on the contact, then L1 · L2 · L3.
  const by = 330;
  const lane = row(16, by, 48, [
    { id: "chatFn", h: H, icon: "lambda", title: "ChatApiFn", sub: "stamps tenant on contact" },
    { id: "qic", h: H, icon: "amazonq", title: "Q in Connect", sub: "[L1] refuses others", tone: "green" },
    { id: "gateway", h: H, icon: "agentcore", title: "Gateway + interceptor", sub: "[L2] resolve + inject", tone: "green" },
    { id: "tool", h: H, icon: "lambda", title: "Transaction tool", sub: "[L3] filter · fail closed", tone: "green" },
  ]);
  const b = byId(lane);
  // Path A (direct search) under API Gateway, shifted right so path B's connector passes it.
  const searchFn = box({ id: "searchFn", x: front.apigw.x + 70, y: 176, h: H, icon: "lambda", title: "SearchApiFn", sub: "forces merchant_id", tone: "green" });
  // Shared sink to the right of both paths.
  const sx = Math.max(b.tool.x + b.tool.w, searchFn.x + searchFn.w) + 116;
  const search = box({ id: "search", x: sx, y: 176, h: by + H - 176, icon: "opensearch", title: "OpenSearch", sub: "private · Pool model" });
  const cx = front.apigw.x + 30; // path B connector leaves API Gateway here, left of SearchApiFn
  const busY = 286;
  const edges: Edge[] = [
    { from: "merchant", to: "cognito", pts: [R(front.merchant), L(front.cognito)] },
    { from: "cognito", to: "spa", pts: [R(front.cognito), L(front.spa)] },
    { from: "spa", to: "apigw", pts: [R(front.spa), L(front.apigw)] },
    {
      from: "apigw",
      to: "searchFn",
      pts: [{ x: searchFn.x + searchFn.w / 2, y: front.apigw.y + H + 2 }, T(searchFn)],
      label: "GET /transactions",
    },
    { from: "searchFn", to: "search", pts: [R(searchFn), { x: search.x - 2, y: R(searchFn).y }], label: "forced filter", color: "green" },
    {
      from: "apigw",
      to: "chatFn",
      pts: [{ x: cx, y: front.apigw.y + H + 2 }, { x: cx, y: busY }, { x: B(b.chatFn).x, y: busY }, T(b.chatFn)],
      seg: 1,
      label: "POST /chat/start",
    },
    { from: "chatFn", to: "qic", pts: [R(b.chatFn), L(b.qic)], color: "green" },
    { from: "qic", to: "gateway", pts: [R(b.qic), L(b.gateway)] },
    { from: "gateway", to: "tool", pts: [R(b.gateway), L(b.tool)], color: "green" },
    { from: "tool", to: "search", pts: [R(b.tool), { x: search.x - 2, y: R(b.tool).y }], label: "filter by tenant", color: "green" },
  ];
  const boxes = [...Object.values(front), searchFn, ...lane, search];
  const vw = search.x + search.w + 16;
  const noteLines = [
    "merchant_id always comes from the validated JWT (or the Connect contact attribute derived from it) — never from the client or the model.",
    "Both read paths hit the same private OpenSearch collection (Pool model): one shared index where every document carries merchant_id.",
  ];
  const noteY = by + H + 28;
  return (
    <Frame
      id="isolation"
      vw={vw}
      vh={noteY + noteH(noteLines.length) + 12}
      aria="End-to-end tenant isolation: Cognito issues an ID token carrying custom:merchant_id; the API Gateway JWT authorizer validates it; the direct search path forces the merchant_id filter from the claim, and the agentic path stamps the tenant onto the Connect contact, then the AgentCore Gateway interceptor injects the trusted merchant_id and the tool always filters by it, failing closed if absent. Both paths read the same private OpenSearch collection."
    >
      <Lane x={16} y={194} text="A · Direct search (merchant app)" />
      <Lane x={B(b.chatFn).x + 16} y={by - 14} text="B · Agentic self-service (chat) — enforcement layers L1 · L2 · L3" />
      <Graph frame="isolation" boxes={boxes} edges={edges} m={M} />
      <Note x={16} y={noteY} w={vw - 32} tone="green" lines={noteLines} />
    </Frame>
  );
}
