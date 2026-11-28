// AgentCore multi-tenant shopping assistant demo -- frontend.
// Talks to the webui backend Lambda (via CloudFront /api/* behavior).
// Never holds any tenant JWT -- only the currently-selected
// tenant/agent/shopper and the current chat's last few turns.

const API_BASE = ""; // same-origin via CloudFront /api/* behavior

let CONFIG = null;
let state = {
  tenantId: null,
  agentId: null,
  modelId: null,
  shopperId: localStorage.getItem("shopperId") || null,
  guidedStep: 0,
  history: [], // this chat's turns, sent back (capped) as context
};

// Mirror the backend's server-side caps (lambda_src/webui_backend/handler.py
// MAX_MESSAGE_CHARS / MAX_HISTORY_TURNS); the server enforces them, this
// just avoids sending requests it would reject.
const MAX_MESSAGE_CHARS = 1000;
const MAX_HISTORY_TURNS = 4;

// The guided demo script: each step sets the brand/agent and pre-fills a
// message. Clicking "Next step" applies the step's brand/agent switch and
// puts the message in the input box (the shopper still hits Send, so the
// chat flow is visible), except the attack step, which runs by itself.
const GUIDED_STEPS = [
  {
    title: "Tell Borealis",
    text: "Tell the Borealis Shopping Assistant your size and budget. Watch it land in the shared namespace.",
    tenant: "borealis", agent: "merch",
    prefill: "I wear size M and my budget is $250 for a jacket.",
  },
  {
    title: "Second agent recalls",
    text: "Switch to Borealis Order Support, a different agent with no chat history. It already knows: same shopper, shared memory.",
    tenant: "borealis", agent: "support",
    prefill: "What do you already know about my size and budget?",
  },
  {
    title: "Other brand: nothing",
    text: "Switch to Northwind with the same shopper id. Its memory is a different namespace, so it knows nothing about you.",
    tenant: "northwind", agent: "support",
    prefill: "What do you know about my size and budget?",
  },
  {
    title: "Attack is blocked",
    text: "As Northwind, forge a request for Borealis's memory. It runs by itself below: the Gateway drops the forged fields, and IAM denies the direct read.",
    tenant: "northwind", agent: "support",
    prefill: null,
    attackTarget: "borealis",
    autoAttack: true,
  },
  {
    title: "Inspect the memory",
    text: "Back at Borealis Order Support: the memory panel shows the shared fact, its namespace and the actorId. Open \"What happened\" under any reply for the full trace.",
    tenant: "borealis", agent: "support",
    prefill: null,
  },
];

function newShopperId() {
  return "shopper" + Math.random().toString(36).slice(2, 10);
}
if (!state.shopperId) {
  state.shopperId = newShopperId();
  localStorage.setItem("shopperId", state.shopperId);
}

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  // textContent, never innerHTML: replies and tool inputs come from the
  // model and can echo shopper text.
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

async function sha256Hex(text) {
  const enc = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", enc);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  // CloudFront's Origin Access Control for the Lambda origin requires a
  // signed payload hash on any request with a body (Lambda Function URLs
  // don't support unsigned payloads when fronted by OAC) -- see
  // deploy/10_webui_cdn.py for the full explanation.
  if (opts.body) {
    headers["x-amz-content-sha256"] = await sha256Hex(opts.body);
  }
  const resp = await fetch(API_BASE + path, { ...opts, headers });
  if (!resp.ok) {
    const text = await resp.text();
    if (resp.status === 429) throw new Error("429: rate limit reached for this IP -- wait a few minutes and retry.");
    throw new Error(`${resp.status}: ${text}`);
  }
  return resp.json();
}

async function loadConfig() {
  CONFIG = await api("/api/config", { method: "GET" });
}

function currentTenant() {
  return CONFIG.tenants.find((t) => t.tenant_id === state.tenantId);
}

function currentAgent() {
  return currentTenant().agents.find((a) => a.agent_id === state.agentId);
}

function populateTenantSelect() {
  const sel = $("tenant-select");
  sel.innerHTML = "";
  for (const t of CONFIG.tenants) {
    const opt = el("option", null, t.tenant_display);
    opt.value = t.tenant_id;
    sel.appendChild(opt);
  }
}

// The attack-target dropdown must NEVER include the currently-selected
// tenant -- targeting your own tenant isn't a cross-tenant attempt at
// all, and showing it as the default made a prior demo embarrassingly
// claim "NOT blocked" for a request that was never actually cross-tenant.
// Refreshed on every tenant switch, not just once at boot.
function populateAttackTargetSelect() {
  const atk = $("attack-target");
  const prevValue = atk.value;
  atk.innerHTML = "";
  for (const t of CONFIG.tenants) {
    if (t.tenant_id === state.tenantId) continue;
    const opt = el("option", null, t.tenant_display);
    opt.value = t.tenant_id;
    atk.appendChild(opt);
  }
  if (Array.from(atk.options).some((o) => o.value === prevValue)) {
    atk.value = prevValue;
  }
}

function populateAgentSelect() {
  const sel = $("agent-select");
  const tenant = currentTenant();
  sel.innerHTML = "";
  for (const a of tenant.agents) {
    const opt = el("option", null, a.agent_display);
    opt.value = a.agent_id;
    sel.appendChild(opt);
  }
  $("agent-select-wrap").style.display = tenant.agents.length > 1 ? "" : "none";
  if (!tenant.agents.some((a) => a.agent_id === state.agentId)) {
    state.agentId = tenant.agents[0].agent_id;
  }
  sel.value = state.agentId;
}

function populateModelSelect() {
  const sel = $("model-select");
  sel.innerHTML = "";
  for (const m of CONFIG.models) {
    const opt = el("option", null, m.label);
    opt.value = m.model_id;
    sel.appendChild(opt);
  }
  state.modelId = CONFIG.default_model;
  sel.value = state.modelId;
}

// Everything that describes "who is talking" -- titles, audiences, the
// namespace map -- re-rendered on every brand/agent/shopper change.
function renderContext() {
  const tenant = currentTenant();
  const agent = currentAgent();
  $("chat-title").textContent = `${tenant.tenant_display} · ${agent.agent_display}`;
  $("memory-title").textContent = `${tenant.tenant_display} · ${agent.agent_display}`;
  $("shopper-id-display").textContent = state.shopperId;
  const others = tenant.agents.filter((a) => a.agent_id !== state.agentId).map((a) => a.agent_display);
  $("shared-audience").textContent = others.length
    ? `Read and written by every ${tenant.tenant_display} agent (here: ${agent.agent_display} + ${others.join(", ")})`
    : `Every ${tenant.tenant_display} agent (this brand has one)`;
  $("private-audience").textContent = `${agent.agent_display} only`;
  renderNamespaceMap();
}

function renderNamespaceMap() {
  const map = $("ns-map");
  map.innerHTML = "";
  for (const t of CONFIG.tenants) {
    const isCurrent = t.tenant_id === state.tenantId;
    const box = el("div", "ns-tenant" + (isCurrent ? " current" : ""));
    const name = el("div", "ns-tenant-name");
    name.appendChild(el("span", null, t.tenant_display));
    name.appendChild(el("span", "caption", isCurrent ? "you are here" : "other brand"));
    box.appendChild(name);
    box.appendChild(el("div", "ns-prefix", `/tenant/${t.tenant_id}/`));
    const rows = [{ key: "shared", who: t.agents.length > 1 ? "all agents" : "whole brand", kind: "shared" }].concat(
      t.agents.map((a) => ({ key: a.agent_id, who: a.agent_display, kind: "private" }))
    );
    for (const r of rows) {
      const open = isCurrent && (r.key === "shared" || r.key === state.agentId);
      const row = el("div", `ns-row ${r.kind} ${open ? "open" : "locked"}`);
      row.title = `/tenant/${t.tenant_id}/${r.key}/${state.shopperId}/ -- ` +
        (open ? "this agent can read and write here" : "out of reach for this agent");
      row.appendChild(el("span", "dot"));
      row.appendChild(el("span", "ns-key", `${r.key}/`));
      row.appendChild(el("span", "ns-who", r.who));
      box.appendChild(row);
    }
    map.appendChild(box);
  }
}

function clearChat() {
  $("chat-log").innerHTML = "";
  state.history = [];
}

// Minimal markdown for model replies: **bold** and "- " bullet lines.
// Builds DOM nodes (textContent only), so nothing the model or shopper
// typed is ever parsed as HTML.
function appendInline(parent, text) {
  text.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      parent.appendChild(el("strong", null, part.slice(2, -2)));
    } else if (part) {
      parent.appendChild(document.createTextNode(part));
    }
  });
}

function renderReply(div, text) {
  let list = null;
  for (const line of text.split("\n")) {
    const bullet = line.match(/^\s*[-*\u2022]\s+(.*)$/);
    if (bullet) {
      if (!list) list = div.appendChild(el("ul", "reply-list"));
      appendInline(list.appendChild(el("li")), bullet[1]);
      continue;
    }
    list = null;
    if (!line.trim()) continue;
    appendInline(div.appendChild(el("p", "reply-p")), line.replace(/^#+\s*/, ""));
  }
}

function addMessage(role, text) {
  const log = $("chat-log");
  const div = el("div", "msg " + role);
  if (role === "assistant") renderReply(div, text || "");
  else div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function renderTracePanel(toolCalls, runtimeIdentity) {
  if (!toolCalls || !toolCalls.length) return null;
  const details = el("details", "trace-panel");
  details.appendChild(el("summary", null, `What happened · ${toolCalls.length} tool call${toolCalls.length > 1 ? "s" : ""}`));
  if (runtimeIdentity) {
    // The agent ran on AgentCore Runtime: show that the client the Runtime
    // verified is the same one the Gateway saw on every tool call.
    const sameJwt = toolCalls.every((tc) => (tc.trace || {}).jwt_client_id === runtimeIdentity.jwt_client_id);
    const block = el("div", "trace-block");
    block.appendChild(el("div", "trace-tool", "AgentCore Runtime"));
    for (const [k, v, warn] of [
      ["Inbound", `JWT client_id ${runtimeIdentity.jwt_client_id} verified by the Runtime's CUSTOM_JWT authorizer`],
      ["Agent", `tenant=${runtimeIdentity.tenant_id} agent=${runtimeIdentity.agent_id} (from the JWT; the request carried no tenant)`],
      ["Forwarded", sameJwt ? "the same JWT to the Gateway on every tool call below" : "MISMATCH: a tool call used a different client", !sameJwt],
    ]) {
      const line = el("div", "trace-line");
      line.appendChild(el("span", "k", k));
      line.appendChild(el("span", "v" + (warn ? " warn" : ""), v));
      block.appendChild(line);
    }
    details.appendChild(block);
  }

  for (const tc of toolCalls) {
    const t = tc.trace || {};
    const block = el("div", "trace-block");
    block.appendChild(el("div", "trace-tool", `${t.tool || tc.name}(${JSON.stringify(tc.input)})`));
    const stripped = t.stripped_fields && t.stripped_fields.length ? t.stripped_fields.join(", ") : null;
    const lines = [
      ["AuthN", `JWT client_id ${t.jwt_client_id || "?"} accepted by the Gateway's CUSTOM_JWT authorizer`],
      ["Context", `tenant=${t.server_tenant_id} agent=${t.server_agent_id} shopper=${t.server_shopper_id} (from the JWT, not the request)`],
      ["Stripped", stripped ? `caller-supplied ${stripped}` : "nothing to strip", !!stripped],
      ["Memory", `${JSON.stringify(t.namespaces_touched)} · actorId ${t.server_actor_id}`],
      ["IAM", `assumed ${t.tenant_role_arn || "?"}`],
    ];
    for (const [k, v, warn] of lines) {
      const line = el("div", "trace-line");
      line.appendChild(el("span", "k", k));
      line.appendChild(el("span", "v" + (warn ? " warn" : ""), v));
      block.appendChild(line);
    }
    details.appendChild(block);
  }
  return details;
}

// Knowledge-base passages behind a policy answer: the ones the reply cites
// first, then any it retrieved but did not cite.
function renderSources(citations) {
  if (!citations || !citations.length) return null;
  const box = el("div", "sources");
  box.appendChild(el("div", "field-label", `Sources · ${tenantDisplay(state.tenantId)} policy knowledge base`));
  for (const c of citations) {
    const item = el("div", "source" + (c.cited ? " cited" : ""));
    const head = el("div", "source-head");
    head.appendChild(el("span", "source-cite", `[${c.cite}]`));
    head.appendChild(el("span", "source-title", `${c.title} · ${c.section}`));
    head.appendChild(el("span", "source-flag", c.cited ? "cited" : "retrieved"));
    item.appendChild(head);
    item.appendChild(el("div", "source-text", c.text));
    box.appendChild(item);
  }
  return box;
}

async function sendMessage(overrideText) {
  const input = $("chat-input");
  const message = (overrideText !== undefined ? overrideText : input.value).trim();
  if (!message) return;
  if (message.length > MAX_MESSAGE_CHARS) {
    addMessage("system", `Message too long (${message.length} chars, max ${MAX_MESSAGE_CHARS}).`);
    return;
  }
  if (overrideText === undefined) input.value = "";
  addMessage("user", message);
  const thinking = addMessage("system", "Thinking…");
  $("chat-send").disabled = true;

  try {
    const out = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        tenant_id: state.tenantId,
        agent_id: state.agentId,
        shopper_id: state.shopperId,
        message,
        model_id: state.modelId,
        history: state.history.slice(-MAX_HISTORY_TURNS),
      }),
    });
    thinking.remove();
    addMessage("assistant", out.reply);
    state.history.push({ role: "user", text: message }, { role: "assistant", text: out.reply.slice(0, MAX_MESSAGE_CHARS) });
    const sources = renderSources(out.citations);
    if (sources) $("chat-log").appendChild(sources);
    const trace = renderTracePanel(out.tool_calls, out.runtime_identity);
    if (trace) $("chat-log").appendChild(trace);
    if (out.pending_approval) {
      renderApprovalDialog(out.pending_approval);
    }
    const host = out.served_by === "agentcore-runtime"
      ? `AgentCore Runtime (${out.runtime_latency_ms} ms)`
      : out.served_by === "lambda-fallback" ? "Lambda fallback (Runtime unavailable)" : "Lambda";
    $("cost-line").textContent =
      `served by ${host} · ${out.model_id} · ${out.input_tokens} in / ${out.output_tokens} out` +
      (out.cache_read_tokens ? ` · cache read ${out.cache_read_tokens}` : "") +
      (out.cache_write_tokens ? ` · cache write ${out.cache_write_tokens}` : "") +
      ` · $${out.cost_usd.toFixed(6)}` +
      (out.guardrail_intervened ? " · guardrail intervened" : "");
    refreshMemory();
  } catch (err) {
    thinking.remove();
    addMessage("system", "Error: " + err.message);
  } finally {
    $("chat-send").disabled = false;
  }
}

function renderApprovalDialog(proposedAction) {
  const log = $("chat-log");
  const box = el("div", "approval-box");
  box.appendChild(el("div", "approval-title", "Human approval required"));
  box.appendChild(el("div", "approval-detail", JSON.stringify(proposedAction)));
  const confirmBtn = el("button", "approval-confirm", "Confirm");
  const cancelBtn = el("button", "approval-cancel", "Cancel");
  confirmBtn.addEventListener("click", async () => {
    box.replaceChildren(el("div", "approval-title", "Processing…"));
    try {
      const out = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({
          tenant_id: state.tenantId,
          agent_id: state.agentId,
          shopper_id: state.shopperId,
          approve_pending: proposedAction,
        }),
      });
      const r = out.approved_result || {};
      box.replaceChildren(el("div", "approval-title", "Confirmed"), el("div", "approval-detail", r.message || JSON.stringify(r)));
    } catch (err) {
      box.textContent = "Error: " + err.message;
    }
  });
  cancelBtn.addEventListener("click", () => {
    box.replaceChildren(el("div", "approval-title", "Cancelled — no action taken."));
  });
  box.appendChild(confirmBtn);
  box.appendChild(cancelBtn);
  log.appendChild(box);
  log.scrollTop = log.scrollHeight;
}

function timeAgo(ts) {
  if (!ts) return "";
  const s = Math.round((Date.now() - new Date(ts).getTime()) / 1000);
  if (Number.isNaN(s)) return "";
  if (s < 60) return `${Math.max(s, 0)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

function renderMemoryList(ul, records, emptyText) {
  ul.innerHTML = "";
  if (!records.length) {
    ul.appendChild(el("li", "empty", emptyText));
    return;
  }
  for (const r of records) {
    const pending = r.status === "pending_indexing";
    const li = el("li");
    li.appendChild(el("span", "fact", r.content?.text || JSON.stringify(r)));
    li.appendChild(el("span", "status " + (pending ? "status-pending" : "status-indexed"), pending ? "Pending" : "Indexed"));
    const when = timeAgo(r.createdAt || r.timestamp);
    li.appendChild(el("span", "meta", (pending ? "fast path · indexing" : "long-term record") + (when ? ` · ${when}` : "")));
    ul.appendChild(li);
  }
}

let _memoryRequest = 0;

async function refreshMemory() {
  const req = ++_memoryRequest;
  const tenantId = state.tenantId;
  try {
    const out = await api(
      `/api/memory?tenant_id=${encodeURIComponent(tenantId)}&agent_id=${encodeURIComponent(state.agentId)}&shopper_id=${encodeURIComponent(state.shopperId)}`,
      { method: "GET" }
    );
    if (req !== _memoryRequest) return; // a newer refresh (e.g. after a switch) owns the panel
    const tenant = currentTenant();
    renderMemoryList($("memory-shared"), out.shared || [], `Nothing ${tenant.tenant_display} knows about this shopper yet.`);
    renderMemoryList($("memory-private"), out.private || [], "No private notes from this agent yet.");
    const ns = out.namespaces || {};
    $("ns-shared").textContent = ns.shared || "—";
    $("ns-private").textContent = ns.private || "—";
    $("actor-id-display").textContent = out.actor_id || "—";
  } catch (err) {
    console.error("refreshMemory failed", err);
  }
}

function tenantDisplay(tenantId) {
  return (CONFIG.tenants.find((t) => t.tenant_id === tenantId) || {}).tenant_display || tenantId;
}

function renderAttackResult(out, fromTenantId, targetTenantId) {
  const resultDiv = $("attack-result");
  resultDiv.className = "attack-result " + (out.blocked ? "blocked" : "danger");
  resultDiv.innerHTML = "";
  resultDiv.appendChild(el("div", "field-label", `${tenantDisplay(fromTenantId)} JWT → ${tenantDisplay(targetTenantId)} memory`));
  resultDiv.appendChild(el("div", "attack-verdict", out.blocked ? "BLOCKED" : "NOT BLOCKED"));
  resultDiv.appendChild(el("p", "attack-summary", out.verdict));

  const compare = el("div", "attack-compare");
  const sent = el("div", "sent");
  sent.appendChild(el("div", "field-label", "Forged arguments sent"));
  sent.appendChild(el("pre", null, JSON.stringify(out.attempted_spoofed_args, null, 1)));
  const used = el("div", "used");
  used.appendChild(el("div", "field-label", "Namespaces actually searched (server-derived)"));
  used.appendChild(el("pre", null, (out.namespaces_actually_searched || []).join("\n")));
  compare.appendChild(sent);
  compare.appendChild(used);
  resultDiv.appendChild(compare);

  const layers = el("div", "attack-layers");
  (out.layer_trace || []).forEach((l, i) => {
    const card = el("div", "attack-layer");
    const [num, ...rest] = l.layer.split(". ");
    card.appendChild(el("div", "num", `Layer ${num || i + 1}`));
    card.appendChild(el("div", "name", rest.join(". ") || l.layer));
    card.appendChild(el("div", "detail", l.detail));
    layers.appendChild(card);
  });
  resultDiv.appendChild(layers);
}

async function runAttack(targetOverride) {
  const atkSel = $("attack-target");
  if (typeof targetOverride === "string" && Array.from(atkSel.options).some((o) => o.value === targetOverride)) {
    atkSel.value = targetOverride;
  }
  const target = atkSel.value;
  const fromTenant = state.tenantId;
  const resultDiv = $("attack-result");
  resultDiv.className = "attack-result pending";
  resultDiv.textContent = "Attempting a cross-brand read…";
  try {
    const out = await api("/api/attack", {
      method: "POST",
      body: JSON.stringify({
        tenant_id: fromTenant,
        agent_id: state.agentId,
        shopper_id: state.shopperId,
        target_tenant_id: target,
      }),
    });
    renderAttackResult(out, fromTenant, target);
  } catch (err) {
    resultDiv.className = "attack-result error";
    resultDiv.textContent = "Error: " + err.message;
  }
}

function renderGuidedStep() {
  const step = GUIDED_STEPS[state.guidedStep];
  $("guided-step-label").textContent = `Step ${state.guidedStep + 1} of ${GUIDED_STEPS.length}`;
  $("guided-step-text").textContent = step.text;
  const list = $("guided-step-list");
  list.innerHTML = "";
  GUIDED_STEPS.forEach((s, i) => {
    const li = el("li", i === state.guidedStep ? "current" : i < state.guidedStep ? "done" : "");
    li.appendChild(el("span", "num", String(i + 1).padStart(2, "0")));
    li.appendChild(document.createTextNode(s.title));
    list.appendChild(li);
  });
  $("guided-next").textContent = state.guidedStep === GUIDED_STEPS.length - 1 ? "Start over" : "Next step";
}

// A brand/agent switch starts a NEW conversation: the chat history sent as
// context must never follow the shopper to another agent, or the next agent
// would "know" things from the transcript instead of from Memory -- which
// would fake exactly what this demo exists to prove. The visible log is kept
// (with a divider) so the guided flow stays readable.
function startNewConversation() {
  state.history = [];
  const tenant = currentTenant();
  const agent = currentAgent();
  if ($("chat-log").children.length) {
    addMessage("system", `New conversation · ${tenant.tenant_display} / ${agent.agent_display} · no chat history carried over`);
  }
}

function switchTo(tenantId, agentId) {
  const before = `${state.tenantId}/${state.agentId}`;
  if (tenantId !== state.tenantId) {
    state.tenantId = tenantId;
    $("tenant-select").value = tenantId;
    populateAgentSelect();
    populateAttackTargetSelect();
  }
  if (agentId && agentId !== state.agentId) {
    state.agentId = agentId;
    $("agent-select").value = agentId;
  }
  renderContext();
  return `${state.tenantId}/${state.agentId}` !== before;
}

function applyGuidedStep() {
  const step = GUIDED_STEPS[state.guidedStep];
  if (switchTo(step.tenant, step.agent)) startNewConversation();
  refreshMemory();
  $("chat-input").value = step.prefill || "";
  if (step.prefill) $("chat-input").focus();
  if (step.autoAttack) {
    runAttack(step.attackTarget);
    $("attack-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

// Apply AND render the SAME step index, always, so the guided-step label
// never gets ahead of (or behind) the action that was actually just
// taken. Step 0 ("Next" wrapping back to the start, or an explicit
// Restart) always starts a fresh shopper id, so stale facts from an
// earlier demo run never leak into a fresh walkthrough.
function goToStep(n) {
  state.guidedStep = n;
  if (n === 0) {
    state.shopperId = newShopperId();
    localStorage.setItem("shopperId", state.shopperId);
    $("actor-id-display").textContent = "—";
    clearChat();
    $("attack-result").textContent = "";
    $("attack-result").className = "attack-result";
  }
  applyGuidedStep();
  renderGuidedStep();
}

function wireUp() {
  $("tenant-select").addEventListener("change", (e) => {
    switchTo(e.target.value, null);
    clearChat();
    refreshMemory();
  });
  $("agent-select").addEventListener("change", (e) => {
    switchTo(state.tenantId, e.target.value);
    clearChat();
    refreshMemory();
  });
  $("model-select").addEventListener("change", (e) => {
    state.modelId = e.target.value;
  });
  $("how-it-works-btn").addEventListener("click", () => $("how-it-works-modal").classList.remove("hidden"));
  $("how-it-works-close").addEventListener("click", () => $("how-it-works-modal").classList.add("hidden"));
  $("how-it-works-modal").addEventListener("click", (e) => {
    if (e.target.id === "how-it-works-modal") $("how-it-works-modal").classList.add("hidden");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $("how-it-works-modal").classList.add("hidden");
  });
  $("chat-send").addEventListener("click", () => sendMessage());
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !$("chat-send").disabled) sendMessage();
  });
  for (const chip of document.querySelectorAll("#suggestions .chip")) {
    chip.addEventListener("click", () => {
      $("chat-input").value = chip.dataset.prompt;
      $("chat-input").focus();
    });
  }
  $("refresh-memory").addEventListener("click", refreshMemory);
  $("attack-button").addEventListener("click", () => runAttack());
  $("new-shopper").addEventListener("click", () => {
    state.shopperId = newShopperId();
    localStorage.setItem("shopperId", state.shopperId);
    $("actor-id-display").textContent = "—";
    clearChat();
    renderContext();
    refreshMemory();
  });
  $("guided-next").addEventListener("click", () => {
    goToStep((state.guidedStep + 1) % GUIDED_STEPS.length);
  });
  $("guided-reset").addEventListener("click", () => {
    goToStep(0);
  });
}

async function boot() {
  await loadConfig();
  populateTenantSelect();
  state.tenantId = CONFIG.tenants[0].tenant_id;
  $("tenant-select").value = state.tenantId;
  populateAgentSelect();
  populateAttackTargetSelect();
  populateModelSelect();
  wireUp();
  goToStep(0);
}

boot().catch((err) => {
  console.error("boot failed", err);
  $("chat-log").textContent = "Could not reach the backend: " + err.message;
});
