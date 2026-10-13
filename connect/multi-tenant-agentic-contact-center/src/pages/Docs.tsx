import { Link } from "react-router-dom";
import { ArchitectureDiagram } from "@/pages/docs/ArchitectureDiagram";
import { AgentCoreFlow, ConnectFlow, IsolationFlow } from "@/pages/docs/FlowDiagrams";

// PUBLIC developer documentation page (no auth) — synthetic API reference +
// interactive solution architecture for the fictional AnyCompanyPay platform. The
// content is 100% self-authored sample data (safe to publish) and mirrors the
// resources actually implemented in this repo (transactions, refunds, disputes,
// dispute -> Amazon Connect Case, support cases, chat, the opt-in Connect modules),
// so it doubles as a knowledge-base corpus.

const SECTIONS: { id: string; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "architecture", label: "Architecture" },
  { id: "features", label: "Connect features" },
  { id: "agentic", label: "Agentic self-service" },
  { id: "escalation", label: "Escalation to a human" },
  { id: "agentcore", label: "Bedrock AgentCore" },
  { id: "connect", label: "Amazon Connect" },
  { id: "routing", label: "Routing & after hours" },
  { id: "screenshare", label: "Screen sharing" },
  { id: "a2a", label: "External AI agents (A2A)" },
  { id: "isolation", label: "Merchant isolation" },
  { id: "authentication", label: "Authentication" },
  { id: "idempotency", label: "Idempotency" },
  { id: "errors", label: "Errors" },
  { id: "transactions", label: "Transactions" },
  { id: "refunds", label: "Refunds" },
  { id: "disputes", label: "Disputes" },
  { id: "support-api", label: "Support API" },
  { id: "multi-tenancy", label: "Multi-tenancy" },
];

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <path d="M6 19V5h6.5c2.9 0 4.9 1.7 4.9 4.4S15.4 13.8 12.5 13.8H9.4V19H6z" fill="currentColor" />
        </svg>
      </div>
      <span className="text-base font-bold text-ink-900">AnyCompanyPay</span>
      <span className="rounded-full bg-ink-100 px-2 py-0.5 text-[11px] font-medium text-ink-500">Docs</span>
    </div>
  );
}

function Code({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg bg-ink-900 p-4 text-[13px] leading-relaxed text-ink-50">
      <code>{children}</code>
    </pre>
  );
}

function Method({ m }: { m: string }) {
  const tone: Record<string, string> = {
    GET: "bg-emerald-100 text-emerald-700",
    POST: "bg-brand-100 text-brand-700",
    PATCH: "bg-amber-100 text-amber-700",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-bold ${tone[m] ?? "bg-ink-100 text-ink-700"}`}>{m}</span>
  );
}

function Endpoint({ method, path, children }: { method: string; path: string; children?: React.ReactNode }) {
  return (
    <div className="mt-4 rounded-lg border border-ink-200">
      <div className="flex items-center gap-3 border-b border-ink-100 bg-ink-50 px-4 py-2.5">
        <Method m={method} />
        <code className="text-sm font-semibold text-ink-800">{path}</code>
      </div>
      {children && <div className="space-y-3 px-4 py-3 text-sm text-ink-600">{children}</div>}
    </div>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-20 border-t border-ink-100 pt-10">
      <h2 className="text-2xl font-bold tracking-tight text-ink-900">{title}</h2>
      <div className="mt-4 space-y-4 text-[15px] leading-relaxed text-ink-600">{children}</div>
    </section>
  );
}

export default function Docs() {
  return (
    <div className="min-h-screen bg-white">
      {/* Public top bar */}
      <header className="sticky top-0 z-20 border-b border-ink-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
          <Link to="/">
            <Logo />
          </Link>
          <nav className="flex items-center gap-4 text-sm font-medium text-ink-500">
            <Link to="/" className="hover:text-ink-900">
              Home
            </Link>
            <Link to="/merchant" className="hover:text-ink-900">
              Merchant
            </Link>
            <Link to="/admin" className="hover:text-ink-900">
              Admin
            </Link>
            <Link
              to="/"
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-white hover:bg-brand-700"
            >
              Sign in
            </Link>
          </nav>
        </div>
      </header>

      <div className="mx-auto flex max-w-6xl gap-10 px-6 py-10">
        {/* In-page section nav */}
        <aside className="hidden w-52 shrink-0 lg:block">
          <nav className="sticky top-20 space-y-1">
            <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-400">
              On this page
            </p>
            {SECTIONS.map((s) => (
              <a
                key={s.id}
                href={`#${s.id}`}
                className="block rounded-lg px-3 py-1.5 text-sm text-ink-600 hover:bg-ink-50 hover:text-ink-900"
              >
                {s.label}
              </a>
            ))}
          </nav>
        </aside>

        {/* Content */}
        <main className="min-w-0 flex-1">
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <strong>Sample documentation.</strong> AnyCompanyPay is a fictional payments platform used for a
            demo/reference project. Endpoints, values, and policies below are synthetic — modeled on
            common payment-provider API patterns, but not affiliated with any provider.
          </div>

          <div className="mt-8">
            <h1 className="text-4xl font-extrabold tracking-tight text-ink-900">AnyCompanyPay Developer Docs</h1>
            <p className="mt-3 text-lg text-ink-500">
              A sample customer-support experience on Amazon Connect: live chat, Q in Connect{" "}
              <strong>agentic self-service</strong> backed by a Bedrock AgentCore Gateway MCP tool,
              escalation to human agents, support cases, and opt-in modules for tier and case-owner
              routing, after-hours follow-up, screen sharing, and hand-off to an external AI agent.
              Everything is tenant-isolated by <code>merchant_id</code>. The resource API below
              (transactions, refunds, disputes) powers the merchant app and the agent's tool.
            </p>
          </div>

          <div className="mt-10 space-y-2">
            <Section id="overview" title="Overview">
              <p>
                This project is a sample of <strong>Amazon Connect + Amazon Bedrock AgentCore</strong>{" "}
                for customer support. Merchants get an AI assistant in live chat that answers questions
                about <em>their own</em> transactions (Q in Connect orchestrates a Bedrock AgentCore
                Gateway MCP tool), can raise and track <strong>support cases</strong>, and can escalate
                to a human agent — with strict per-merchant tenant isolation throughout. The AI
                assistant answers 24/7; human agents work business hours, and requests outside those
                hours can be logged as a case with a scheduled follow-up.
              </p>
              <p>
                The core (web app, Connect instance, agentic self-service) is always deployed. Three
                Connect modules are <strong>opt-in and additive</strong>: routing and after-hours
                backlog (<code>connect-routing/</code>), screen sharing (<code>connect-screenshare/</code>),
                and hand-off to an external AI agent over A2A (<code>connect-a2a/</code>).
              </p>
              <p>
                Underneath sits a small resource-oriented API. Each core object —{" "}
                <strong>Transaction</strong>, <strong>Refund</strong>, and <strong>Dispute</strong> — is
                its own resource with its own endpoints and lifecycle. It backs the merchant app and is
                the data source the agentic <code>query_transactions</code> tool reads from.
              </p>
              <p>Base URL (per environment; injected into the app's runtime config):</p>
              <Code>{`https://{api-id}.execute-api.{region}.amazonaws.com`}</Code>
              <p>
                All requests are over HTTPS and must be authenticated (see{" "}
                <a href="#authentication" className="text-brand-600 hover:underline">
                  Authentication
                </a>
                ). Amounts are decimal in the transaction's currency (e.g. <code>100.00</code>), and
                identifiers are opaque strings with a type prefix (<code>txn_</code>, <code>ref_</code>,
                <code> dp_</code>).
              </p>
            </Section>

            <Section id="architecture" title="Solution architecture">
              <p>
                AnyCompanyPay runs entirely on AWS with a private-by-default posture: no public load balancer,
                no open Lambda, and authorization enforced at the gateway and backend rather than in the
                browser. The diagram shows both use cases — the live-chat agentic self-service path and
                the support-cases path — sharing Amazon Cognito, the API layer, and one Amazon Connect
                instance.
              </p>
              <ArchitectureDiagram />
              <p>
                <strong>Agentic self-service:</strong> a merchant's chat reaches a Q in Connect
                orchestrator, which calls the <code>query_transactions</code> MCP tool through a{" "}
                <strong>Bedrock AgentCore Gateway</strong>. A request interceptor injects the trusted
                tenant so the tool only ever returns the caller's rows (see{" "}
                <a href="#agentic" className="text-brand-600 hover:underline">
                  Agentic self-service
                </a>
                ).
              </p>
              <p>
                <strong>Data plane:</strong> Amazon Aurora PostgreSQL is the transactional{" "}
                <em>system of record</em> — it holds the <code>transactions</code> (plus{" "}
                <code>refunds</code> and <code>disputes</code>) tables and runs in a private, isolated
                VPC with no internet route. Reads for search never hit Aurora directly. Instead, an
                AWS-managed <strong>zero-ETL</strong> (Amazon OpenSearch Ingestion / OSIS) pipeline keeps
                a private Amazon OpenSearch Serverless collection continuously in sync with Aurora: an
                initial snapshot is exported to S3 and loaded, then ongoing changes stream over
                PostgreSQL logical replication (WAL) as upserts and deletes. Every indexed document
                carries the tenant key <code>merchant_id</code> replicated from Aurora, so the agent's
                tool queries OpenSearch for fast, tenant-isolated search while Aurora stays the
                authoritative write store.
              </p>
              <p>
                <strong>Support plane:</strong> Amazon Connect provides support cases (Connect Cases),
                live chat (Chat SDK), Customer Profiles, and the agent contact panel (CCP). Opening a
                dispute automatically files a tenant-tagged Connect Case; a merchant can also start a
                chat about a specific case, and the transcript is written back as a case comment.
              </p>
              <p>
                <strong>Isolation:</strong> every request carries a Cognito ID token; the tenant
                (<code>merchant_id</code>) is read from the validated token — never the request body or
                the model — and enforced server-side on every read and write.
              </p>
            </Section>

            <Section id="features" title="Amazon Connect features used">
              <p>
                Every Amazon Connect capability the sample uses, what it does here, and which module
                deploys it. Modules marked <em>opt-in</em> are additive: deploying them does not change
                the core.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-ink-200 text-left text-ink-500">
                      <th className="py-2 pr-4 font-semibold">Feature</th>
                      <th className="py-2 pr-4 font-semibold">How the sample uses it</th>
                      <th className="py-2 font-semibold">Module</th>
                    </tr>
                  </thead>
                  <tbody className="text-ink-600">
                    {[
                      ["Instance + embedded CCP (Streams API)", "Agents work from the CCP embedded in the admin workspace", "core"],
                      ["Chat (StartChatContact, Chat SDK)", "Floating live chat and \u201cChat about this case\u201d; tenant stamped from the token", "core"],
                      ["Contact flows", "Inbound, case, routed, after-hours task, screen-share and A2A flows, all in CDK", "core + opt-in"],
                      ["Amazon Connect Cases", "Support cases with custom fields; merchants see only their own; transcripts as comments", "core"],
                      ["Customer Profiles", "Account profile per merchant (with tier) + individual profiles per user", "core"],
                      ["Q in Connect AI agents", "Self-service orchestrator with a custom prompt, MCP tool, Escalate / Complete tools", "core"],
                      ["Amazon Lex V2", "Hands chat to the AI agent; a Yes / No bot for \u201clog a case?\u201d", "core + routing"],
                      ["Queues, routing profiles, priority", "Tier queues (VIP / key / shared) at priority 1 / 2 / 5", "routing (opt-in)"],
                      ["Routing criteria (preferred agent)", "A chat on an open case goes to the case owner first", "routing (opt-in)"],
                      ["Hours of operation", "AI 24/7; human hand-off only in business hours; next opening for follow-ups", "routing (opt-in)"],
                      ["Tasks (scheduled)", "After-hours requests become a case + one follow-up task at the next opening", "routing (opt-in)"],
                      ["In-app web calling + screen sharing", "Merchant shares their screen from a case; agent watches in the CCP", "screen share (opt-in)"],
                      ["Agent-to-agent (A2A) collaboration", "Chat handed to an external AI agent over the Connect A2A extension", "A2A (opt-in)"],
                    ].map(([f, how, mod]) => (
                      <tr key={f} className="border-b border-ink-100 align-top">
                        <td className="py-2 pr-4 font-semibold text-ink-800">{f}</td>
                        <td className="py-2 pr-4">{how}</td>
                        <td className="py-2 text-xs text-ink-500">{mod}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>

            <Section id="agentic" title="Agentic self-service">
              <p>
                In live chat, a merchant can ask about their own payments in natural language
                (“how many failed payments do I have?”). Amazon Lex routes the message to a{" "}
                <strong>Q in Connect</strong> orchestrator (an ORCHESTRATION AI agent), which decides
                when to call the <code>query_transactions</code> tool exposed through a{" "}
                <strong>Bedrock AgentCore Gateway</strong> (MCP).
              </p>
              <p>
                The single most important rule is that the tenant is <strong>never</strong> supplied by
                the model. Tenant resolution and injection happen server-side, in three layers:
              </p>
              <ul className="list-disc space-y-1 pl-5">
                <li>
                  <strong>Prompt:</strong> the orchestration prompt is instructed to never ask for or
                  pass a <code>merchant_id</code>, and to refuse cross-tenant requests.
                </li>
                <li>
                  <strong>Gateway interceptor:</strong> on every <code>tools/call</code> it reads the
                  Connect contact id from the request headers, calls{" "}
                  <code>connect:GetContactAttributes</code> to resolve the trusted{" "}
                  <code>merchant_id</code>, strips any model-supplied tenant keys, and injects the
                  trusted value under a reserved argument.
                </li>
                <li>
                  <strong>Tool (defense in depth):</strong> the tool trusts <em>only</em> that
                  injected value, always filters the OpenSearch query by <code>merchant_id</code>{" "}
                  (query-time "pool" isolation), and <strong>fails closed</strong> if it is absent.
                </li>
              </ul>
              <p>
                The tenant value originates as a Connect <strong>contact attribute</strong> stamped by
                the chat-start Lambda from the merchant's verified Cognito token — so a prompt injection
                cannot make one merchant read another's data.
              </p>
            </Section>

            <Section id="escalation" title="Escalation to a human">
              <p>
                The assistant can only <em>look up</em> a merchant's transactions and explain policy.
                For anything that needs a person — initiating a refund, responding to a dispute,
                changing payout details — it says so and <strong>asks first</strong>:
              </p>
              <Code>{`Merchant:  Please refund my last failed payment.
Assistant: I can't initiate refunds in this chat. Your last failed payment was
           txn_4be1 (SGD 120.00, failed on 2 Oct).
           Would you like me to escalate this to a human agent?
           [ Yes, connect me to an agent ]  [ No, thanks ]`}</Code>
              <ul className="list-disc space-y-1 pl-5">
                <li>
                  <strong>Yes</strong> calls the orchestrator's <code>Escalate</code> tool
                  (return-to-control). The flow reads <code>$.Lex.SessionAttributes.Tool</code> and hands
                  the chat to a human queue.
                </li>
                <li>
                  <strong>No</strong> keeps the conversation with the assistant; it does not ask again for
                  the same request.
                </li>
                <li>
                  An explicit &ldquo;talk to a human&rdquo; escalates straight away, without the
                  question.
                </li>
              </ul>
              <p>
                The chat widget renders the question's Yes / No as buttons. The assistant never claims
                an agent is joining — the flow decides that, because it knows whether agents are
                available (see{" "}
                <a href="#routing" className="text-brand-600 hover:underline">
                  Routing &amp; after hours
                </a>
                ).
              </p>
            </Section>

            <Section id="agentcore" title="Bedrock AgentCore">
              <p>
                <strong>Amazon Bedrock AgentCore</strong> is what turns the merchant's chat into a
                safe, tool-using agent. Instead of exposing an API for the model to call, the{" "}
                <code>query_transactions</code> capability is published as a <strong>Model Context
                Protocol (MCP)</strong> tool behind an <strong>AgentCore Gateway</strong>. Q in Connect
                orchestrates the conversation and decides when to call the tool; the Gateway (not the
                model) is where identity and tenant enforcement happen.
              </p>
              <AgentCoreFlow />
              <p>Three pieces make it work — two Lambdas plus the managed Gateway:</p>
              <ul className="list-disc space-y-1 pl-5">
                <li>
                  <strong>AgentCore Gateway</strong> — the managed MCP endpoint. Inbound auth is{" "}
                  <code>CUSTOM_JWT</code> with <strong>Amazon Connect</strong> as the OIDC issuer (not
                  Cognito): the Gateway's discovery URL is the Connect instance's{" "}
                  <code>/.well-known/openid-configuration</code>, and the token Connect sends carries
                  the Gateway id in its <code>aud</code> claim.
                </li>
                <li>
                  <strong>Request interceptor</strong> (<code>GatewayInterceptorFn</code>) — the tenant
                  gate. It runs on <em>every</em> request; non-<code>tools/call</code> methods (like the{" "}
                  <code>tools/list</code> discovery call) pass through unchanged so discovery keeps
                  working. On <code>tools/call</code> it strips any model-supplied tenant keys, resolves
                  the trusted <code>merchant_id</code> from the Connect contact via{" "}
                  <code>GetContactAttributes</code>, and injects it as{" "}
                  <code>__trusted_merchant_id</code>.
                </li>
                <li>
                  <strong>Tool target</strong> (<code>TransactionToolFn</code>) — a read-only Lambda in
                  the Aurora VPC that queries the private OpenSearch collection over SigV4. It reads the
                  tenant <em>only</em> from <code>__trusted_merchant_id</code>, always filters by it, and{" "}
                  <strong>fails closed</strong> if it is absent. <code>merchant_id</code> is deliberately{" "}
                  <em>not</em> in the tool's input schema.
                </li>
              </ul>
              <p>
                <strong>Build and deploy.</strong> The Lambdas, IAM and the Gateway are CDK; the AI agent
                and its prompt are created by a script (the AI agent API has no CloudFormation support).
                Region defaults to <code>us-west-2</code>; override with <code>AWS_REGION</code>.
              </p>
              <Code>{`# 1. Gateway + tool and interceptor Lambdas
cd connect-ai-agent && npm install && bash deploy.sh

# 2. Two one-time console steps in the Connect admin website:
#    a. Q in Connect -> Domains -> Add domain (AWS owned key)
#    b. Third-party applications -> Add integration -> MCP server -> the gateway

# 3. Security-profile MCP grant (needs step 2b), the AI agent, then the Lex bot
WITH_SECURITY_PROFILE=1 bash deploy.sh
bash provision-ai-agent.sh --apply --set-default
bash deploy-lex.sh`}</Code>
              <p>
                <code>provision-ai-agent.sh</code> creates or updates the orchestration prompt and the
                ORCHESTRATION AI agent with its three tools (<code>query_transactions</code>,{" "}
                <code>Complete</code>, <code>Escalate</code>). When the prompt text changes it publishes
                a new prompt version and a new agent version. It copies the agent's{" "}
                <strong>security profiles onto the new version</strong> (they attach per version, and
                without them the MCP tool call is refused) and binds that version as the{" "}
                <strong>Self-Service</strong> orchestrator.
              </p>
              <p>
                Two gotchas. Publishing the agent in the console can reset the Self-Service binding,
                so re-run <code>provision-ai-agent.sh --apply --set-default</code> afterwards. And the
                Q in Connect (AI) domain must use the <strong>AWS-owned KMS key</strong> (or a CMK whose
                policy grants <code>connect.amazonaws.com</code>); otherwise chat fails with
                &ldquo;Amazon Lex could not access your Q In Connect Assistant.&rdquo;
              </p>
            </Section>

            <Section id="connect" title="Amazon Connect">
              <p>
                <strong>Amazon Connect</strong> is the customer-experience layer. One Connect instance
                provides four capabilities, all tied together by <code>merchant_id</code>: live chat,
                the Q in Connect agentic self-service described above, support cases (Connect Cases), and
                the agent contact panel (CCP). The chat instance, queue, routing profile, Cases domain,
                Customer Profiles, and contact flows are all provisioned in CDK (
                <code>AnyCompanyPayConnectStack</code>).
              </p>
              <ConnectFlow />
              <p>
                <strong>Starting a chat.</strong> The SPA calls <code>POST /chat/start</code>{" "}
                (<code>ChatApiFn</code>), which derives the caller's identity from the validated Cognito
                JWT and calls <code>StartChatContact</code>. The contact attributes (
                <code>merchant_id</code>, <code>merchant_name</code>, <code>email</code>, and a
                server-verified <code>case_id</code>) are sourced <em>only</em> from the token — never
                the request body — and the contact is flagged <code>AUTHENTICATED</code>. The response
                is a participant token scoped to that one contact.
              </p>
              <p>
                <strong>Routing.</strong> The inbound contact flow greets the merchant and then either:
              </p>
              <ul className="list-disc space-y-1 pl-5">
                <li>
                  <strong>Self-service:</strong> a Lex "Get customer input" block (using the{" "}
                  <code>AMAZON.QinConnectIntent</code>) hands the turn to the Q in Connect orchestrator,
                  which can call the AgentCore tool and reply — looping for a multi-turn conversation.
                </li>
                <li>
                  <strong>Human handoff:</strong> when the orchestrator returns <code>Escalate</code>,
                  the flow sets the support queue as the target and transfers the contact, which the{" "}
                  <code>CHAT</code> routing profile delivers to an agent in the embedded CCP.
                </li>
              </ul>
              <p>
                With the routing module deployed, merchant chats use the routed flow instead, which adds
                tier priority, case-owner routing and business-hours handling (next section).
              </p>
              <p>
                <strong>Cases and transcripts.</strong> A merchant can start a chat from a specific
                support case; that path uses a dedicated case flow, stamps the verified{" "}
                <code>case_id</code> onto the contact, and when the chat ends the transcript is written
                back to the case as a comment. Customer side uses the Chat JS SDK; the agent side uses
                the Streams CCP — kept on separate routes to avoid a shared <code>window.connect</code>{" "}
                conflict.
              </p>
            </Section>

            <Section id="routing" title="Routing & after hours (opt-in)">
              <p>
                <code>connect-routing/</code> switches merchant chats to a routed flow. The{" "}
                <strong>AI assistant is available 24/7</strong>; business hours (default Mon–Fri
                08:00–18:00, Asia/Singapore) only gate <em>human</em> agents.
              </p>
              <p>
                <strong>Tier routing.</strong> A contact-context Lambda reads the merchant's tier from
                their Customer Profiles account profile: <code>VIP</code>, <code>key</code> or{" "}
                <code>shared</code>. In hours, an escalated chat goes to that tier's queue at contact
                priority 1, 2 or 5, so VIP merchants are answered first.
              </p>
              <p>
                <strong>Case-owner routing.</strong> A chat started from an <em>open</em> case is offered
                to the case owner first (a preferred-agent routing criterion with an expiry), then falls
                back to the tier queue — at priority 1, ahead of new contacts.
              </p>
              <p>
                <strong>After hours.</strong> When a merchant asks for a human while agents are offline,
                the chat asks:
              </p>
              <Code>{`Our support agents are offline right now (Mon–Fri 08:00–18:00).
Would you like me to log this as a support case so an agent can follow up when we open?
[ Yes, log a case ]  [ No, thanks ]`}</Code>
              <ul className="list-disc space-y-1 pl-5">
                <li>
                  <strong>Yes</strong> creates a <strong>new</strong> case, &ldquo;After-hours request —
                  &lt;merchant&gt;&rdquo;, with a readable note, plus one <strong>scheduled task</strong>{" "}
                  (<code>StartTaskContact</code>) for the next opening. The next opening comes from{" "}
                  <code>GetEffectiveHoursOfOperations</code>, so holiday overrides apply. At opening, the
                  tasks are offered to backlog agents in tier order (VIP → key → shared). The merchant sees
                  the case under <strong>Support</strong>.
                </li>
                <li>
                  A second request the <strong>same night</strong> goes on that case, with no second
                  follow-up. Older, unrelated open cases are never reused.
                </li>
                <li>
                  <strong>No</strong> logs nothing, says when agents are available, and returns to the AI
                  assistant.
                </li>
              </ul>
              <p>
                Closures longer than the 6-day scheduling limit re-schedule themselves; scheduling
                failures are commented on the case and published to an SNS alert topic.
              </p>
            </Section>

            <Section id="screenshare" title="Screen sharing (opt-in)">
              <p>
                <code>connect-screenshare/</code> adds a <strong>Share screen</strong> button to a
                merchant's case. It starts an Amazon Connect <strong>web call</strong> (
                <code>StartWebRTCContact</code>, screen sharing allowed for the customer) and shares the
                merchant's screen with the Amazon Chime SDK. The agent answers in the embedded CCP or the
                Connect agent workspace and sees the screen live.
              </p>
              <ul className="list-disc space-y-1 pl-5">
                <li>
                  The start API stamps the tenant from the token and checks that the case belongs to the
                  merchant (<code>403</code> otherwise).
                </li>
                <li>
                  The app polls <code>GET /screenshare/status/&#123;contactId&#125;</code> to show when an
                  agent has really connected (Connect&rsquo;s media participant joins the meeting before
                  any agent answers, so meeting presence is not used).
                </li>
                <li>
                  Agents need <code>VideoContact.Access</code> (the{" "}
                  <code>anycompany-pay-video-agent</code> security profile).
                </li>
              </ul>
            </Section>

            <Section id="a2a" title="External AI agents over A2A (opt-in)">
              <p>
                <code>connect-a2a/</code> shows Amazon Connect <strong>agent-to-agent (A2A)
                collaboration</strong>: a chat is handed to a <em>third-party</em> AI agent over a
                WebSocket, with no PSTN transfer. A mock &ldquo;Level3&rdquo; partner on API Gateway
                WebSocket + Lambda implements the Connect A2A protocol contract, so the demo is
                self-contained.
              </p>
              <ul className="list-disc space-y-1 pl-5">
                <li>
                  <strong>Customer side:</strong> the partner API key in Secrets Manager (customer managed
                  KMS key Connect can decrypt), an AppIntegrations application of type{" "}
                  <code>A2A_SERVER</code>, a security profile that allows it as <code>THIRD_PARTY</code>,
                  and an orchestrator AI agent with an immediate <code>handoffAgentConfiguration</code>.
                </li>
                <li>
                  <strong>Partner side:</strong> <code>INIT_SESSION</code>, text turns, an OTLP{" "}
                  <code>TRACING_SPAN</code> per turn (required), and <code>FINISH</code> with{" "}
                  <code>COMPLETE</code> or <code>ESCALATE</code>. Before it finishes, the partner writes its
                  intent, summary and outcome to the contact.
                </li>
                <li>
                  <strong>Routing:</strong> <code>ESCALATE</code> — or any partner failure — goes to the
                  support queue, and the human agent sees the partner&rsquo;s summary.
                </li>
              </ul>
              <Code>{`cd connect-a2a && bash deploy.sh
# then: Connect admin website -> Test chat -> flow "anycompany-pay-chat-a2a"
#   "What is the payout time?"  -> partner answers
#   "agent"                      -> ESCALATE -> support queue (l3_* attributes on the contact)
#   "bye"                        -> COMPLETE -> chat ends`}</Code>
              <p>
                It is configured through the API only (CloudFormation does not support{" "}
                <code>A2A_SERVER</code> applications yet), needs the Connect Customer tier, and voice
                streaming would need a persistent low-latency partner server rather than Lambda.
              </p>
            </Section>

            <Section id="isolation" title="Merchant isolation">
              <p>
                Every merchant is a tenant identified by a single value: <code>custom:merchant_id</code>,
                a claim in the Cognito <em>ID token</em>. The invariant is that this value is{" "}
                <strong>always taken from the validated token server-side</strong> (or from a Connect
                contact attribute derived from it) — never from the request body, a query string, or the
                language model. The diagram traces it from login all the way to the database on both read
                paths.
              </p>
              <IsolationFlow />
              <p>
                <strong>The common front.</strong> Sign-in is the Cognito Hosted UI (Authorization Code
                + PKCE). The SPA attaches the ID token as a bearer credential; the API Gateway HTTP API
                validates it with a <strong>Cognito JWT authorizer</strong> (signature, issuer,
                audience, expiry) before any Lambda runs. Each Lambda then reads the caller's role and
                tenant from <code>requestContext.authorizer.jwt.claims</code>.
              </p>
              <p>
                <strong>Path A — direct search.</strong> <code>SearchApiFn</code> (inside the Aurora VPC)
                forces the tenant filter from the claim: for a merchant it always adds{" "}
                <code>term merchant_id.keyword = &lt;claim&gt;</code>; only an admin may widen or narrow
                with <code>?merchant_id=</code>. A merchant token with no tenant tag is rejected with{" "}
                <code>403</code>.
              </p>
              <p>
                <strong>Path B — agentic self-service.</strong> The tenant rides the chat instead of the
                query, and AgentCore enforces it in three independent layers:
              </p>
              <ul className="list-disc space-y-1 pl-5">
                <li>
                  <strong>L1 — Prompt.</strong> The orchestration prompt wraps user messages as untrusted
                  input and instructs the agent to never ask for or pass a <code>merchant_id</code> and
                  to decline cross-tenant requests.
                </li>
                <li>
                  <strong>L2 — Gateway interceptor.</strong> On every <code>tools/call</code> it strips
                  all model-supplied tenant keys (including a forged{" "}
                  <code>__trusted_merchant_id</code>), resolves the real tenant from the Connect contact
                  via <code>GetContactAttributes</code>, and injects it under the reserved key.
                </li>
                <li>
                  <strong>L3 — Tool, fail-closed.</strong> The tool reads the tenant <em>only</em> from
                  the reserved <code>__trusted_merchant_id</code>, always applies the{" "}
                  <code>merchant_id</code> filter, and refuses to run (returns an error, never unfiltered
                  data) if it is absent.
                </li>
              </ul>
              <p>
                The contact attribute that L2 reads is stamped by <code>ChatApiFn</code> at chat start
                from the merchant's verified token — so a prompt injection can never make one merchant
                read another's data. This was validated end-to-end: a prompt-injection attempt was
                refused with no tool call, forged tenant keys were stripped, and the tool failed closed on
                decoys — while Luxe Living and NovaMart each saw only their own transactions.
              </p>
              <p>
                <strong>The same rule everywhere.</strong> Cases are filtered to the caller's tenant
                (list is filtered, create forces <code>merchant_id</code> from the token, and read/update
                return <code>403</code> across tenants). The commerce API on Aurora scopes every query
                with <code>WHERE merchant_id = &lt;claim&gt;</code>. And the OpenSearch collection both
                read paths hit is the same private, VPC-only collection using the "pool" model — one
                shared index where every document carries <code>merchant_id</code>.
              </p>
            </Section>

            <Section id="authentication" title="Authentication">
              <p>
                AnyCompanyPay uses <strong>bearer authentication</strong> with an Amazon Cognito <em>ID token</em>.
                Sign in through the hosted UI (Authorization Code + PKCE); the SPA attaches the token to
                every API call. The token carries the caller's role (<code>admin</code> or{" "}
                <code>merchant</code>) and, for merchants, their tenant (<code>custom:merchant_id</code>).
              </p>
              <Code>{`GET /transactions
Authorization: Bearer <cognito-id-token>`}</Code>
              <ul className="list-disc space-y-1 pl-5">
                <li>Unauthenticated requests are rejected with <code>401</code> at the gateway.</li>
                <li>
                  A <strong>merchant</strong> token is scoped to its own tenant; a merchant can only read
                  and write its own resources.
                </li>
                <li>
                  An <strong>admin</strong> token may act across tenants (and must name the tenant when
                  creating a transaction on a merchant's behalf).
                </li>
              </ul>
            </Section>

            <Section id="idempotency" title="Idempotency">
              <p>
                Write endpoints that create money-moving objects accept an <code>idempotencyKey</code>.
                Sending the same key again returns the original object instead of creating a duplicate —
                safe to retry on network errors.
              </p>
              <Code>{`POST /refunds
{
  "transactionId": "txn_9f2c...",
  "amount": 40.00,
  "idempotencyKey": "refund-attempt-8f3a1c"
}
// A repeat with the same key returns the same refund ("idempotent": true).`}</Code>
            </Section>

            <Section id="errors" title="Errors">
              <p>AnyCompanyPay uses conventional HTTP status codes:</p>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-ink-200 text-left text-ink-500">
                      <th className="py-2 pr-4 font-semibold">Code</th>
                      <th className="py-2 font-semibold">Meaning</th>
                    </tr>
                  </thead>
                  <tbody className="text-ink-600">
                    {[
                      ["200 / 201", "Success. 201 when a new resource was created."],
                      ["400", "Invalid request — a required field is missing or malformed."],
                      ["401", "Missing or invalid authentication (rejected at the gateway)."],
                      ["403", "Authenticated but not allowed (wrong role, or another tenant's resource)."],
                      ["404", "Resource not found in your tenant."],
                      ["409", "Business-rule conflict (e.g. refund exceeds balance, payment disputed)."],
                      ["500", "Unexpected server error."],
                    ].map(([c, m]) => (
                      <tr key={c} className="border-b border-ink-100">
                        <td className="py-2 pr-4 font-mono text-xs font-semibold text-ink-800">{c}</td>
                        <td className="py-2">{m}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Code>{`{ "message": "amount exceeds refundable balance (60 SGD)", "remaining": 60 }`}</Code>
            </Section>

            <Section id="transactions" title="Transactions">
              <p>
                A <strong>Transaction</strong> represents a payment. Merchants create transactions under
                their own tenant automatically; admins must name the merchant.
              </p>
              <Endpoint method="POST" path="/transactions">
                <p>Create a payment. Merchant identity is taken from the token for merchant callers.</p>
                <Code>{`POST /transactions
{
  "amount": 100.00,
  "currency": "SGD",
  "customerName": "Hana Garcia",
  "paymentMethod": "card",
  "status": "succeeded"
}

201 Created
{
  "transaction": {
    "transactionId": "txn_9f2c8ab1...",
    "merchantId": "mch_luxe",
    "amount": 100.00,
    "currency": "SGD",
    "status": "succeeded",
    "paymentMethod": "card",
    "createdAt": "2026-05-01T09:20:00Z"
  }
}`}</Code>
                <p>
                  <code>status</code> is one of <code>succeeded</code>, <code>pending</code>,{" "}
                  <code>in_progress</code>, <code>failed</code>, <code>refunded</code>,{" "}
                  <code>authorized</code>.
                </p>
              </Endpoint>
              <Endpoint method="GET" path="/transactions?q=&status=&limit=">
                <p>Search your transactions (tenant-scoped). Filter by status or free-text query.</p>
              </Endpoint>
              <Endpoint method="GET" path="/transactions/{id}">
                <p>Retrieve a single transaction by id.</p>
              </Endpoint>
            </Section>

            <Section id="refunds" title="Refunds">
              <p>
                A <strong>Refund</strong> returns funds for a payment. A payment can have multiple partial
                refunds; the total can never exceed the payment amount. Refunds are their own resource
                with their own lifecycle.
              </p>
              <Endpoint method="POST" path="/refunds">
                <p>Create a refund. Enforced rules:</p>
                <ul className="list-disc space-y-1 pl-5">
                  <li>
                    <strong>Refundable balance:</strong> <code>amount</code> defaults to the remaining
                    balance (payment amount minus prior refunds) and may not exceed it (<code>409</code>).
                  </li>
                  <li>
                    <strong>Refundable state:</strong> the payment must be <code>succeeded</code> or
                    partially <code>refunded</code>.
                  </li>
                  <li>
                    <strong>No refund on a disputed payment:</strong> if the payment has an active dispute
                    (<code>needs_response</code>, <code>under_review</code>) or was <code>lost</code>, the
                    refund is rejected (<code>409</code>) to avoid double-loss.
                  </li>
                  <li>
                    <strong>Idempotency:</strong> supply <code>idempotencyKey</code> to make retries safe.
                  </li>
                </ul>
                <Code>{`POST /refunds
{ "transactionId": "txn_9f2c...", "amount": 40.00, "reason": "requested_by_customer" }

201 Created
{ "refund": { "refundId": "ref_1a2b...", "amount": 40.00, "status": "succeeded" } }`}</Code>
                <p>
                  When refunds reach the full amount, the payment's status flips to <code>refunded</code>.
                </p>
              </Endpoint>
              <Endpoint method="GET" path="/refunds?transaction_id=">
                <p>List refunds in your tenant, optionally filtered by transaction.</p>
              </Endpoint>
              <Endpoint method="GET" path="/refunds/{id}">
                <p>Retrieve a single refund.</p>
              </Endpoint>
            </Section>

            <Section id="disputes" title="Disputes">
              <p>
                A <strong>Dispute</strong> (chargeback) is raised when a cardholder or bank contests a
                payment. The merchant responds with evidence before a deadline. Opening a dispute
                <strong> automatically files a tenant-tagged Amazon Connect Case</strong> so it is tracked
                by support and visible to the merchant.
              </p>
              <Endpoint method="POST" path="/disputes">
                <p>Open a dispute on a payment; returns the dispute with a linked Connect Case id.</p>
                <Code>{`POST /disputes
{ "transactionId": "txn_9f2c...", "reason": "product_not_received" }

201 Created
{
  "dispute": {
    "disputeId": "dp_7c1d...",
    "status": "needs_response",
    "amount": 250.00,
    "dueBy": "2026-05-08T09:20:00Z",
    "caseId": "7dc1b3d6-6ead-3667-82fa-4e3cf1b114a2"
  }
}`}</Code>
                <p>
                  <code>status</code> transitions: <code>needs_response</code> →{" "}
                  <code>under_review</code> → <code>won</code> / <code>lost</code>.
                </p>
              </Endpoint>
              <Endpoint method="POST" path="/disputes/{id}/evidence">
                <p>Submit evidence; moves the dispute to <code>under_review</code>.</p>
                <Code>{`POST /disputes/{id}/evidence
{ "evidence": "Tracking shows delivered; signature on file." }`}</Code>
              </Endpoint>
              <Endpoint method="GET" path="/disputes?status=&transaction_id=">
                <p>List disputes in your tenant.</p>
              </Endpoint>
              <Endpoint method="GET" path="/disputes/{id}">
                <p>Retrieve a single dispute (including its linked <code>caseId</code>).</p>
              </Endpoint>
            </Section>

            <Section id="support-api" title="Support API">
              <p>
                Support cases, chat and screen sharing have their own API (Amazon Connect stacks), with
                the same Cognito bearer token. The tenant always comes from the token: merchants only see
                and change their own cases.
              </p>
              <Endpoint method="GET" path="/cases">
                <p>
                  List cases. For a merchant the Cases search is filtered by their{" "}
                  <code>merchant_id</code> and paged; an admin sees all tenants.
                </p>
              </Endpoint>
              <Endpoint method="POST" path="/cases">
                <p>Create a case. For merchants the tenant fields are forced from the token.</p>
                <Code>{`POST /cases
{ "title": "Payout missing for 3 Oct", "summary": "Settlement not received", "priority": "High" }`}</Code>
              </Endpoint>
              <Endpoint method="GET" path="/cases/{id}">
                <p>Read a case with its comments; <code>403</code> for another tenant&rsquo;s case.</p>
              </Endpoint>
              <Endpoint method="PATCH" path="/cases/{id}">
                <p>
                  Update <code>status</code>, <code>priority</code> or <code>summary</code>;{" "}
                  <code>403</code> across tenants.
                </p>
              </Endpoint>
              <Endpoint method="POST" path="/cases/{id}/comments">
                <p>Add a comment to a case.</p>
              </Endpoint>
              <Endpoint method="POST" path="/chat/start">
                <p>
                  Start a live chat. Pass <code>caseId</code> to chat about a case (its tenant is checked
                  first). Contact attributes (<code>merchant_id</code>, <code>merchant_name</code>,{" "}
                  <code>email</code>, <code>case_id</code>) come only from the token.
                </p>
                <Code>{`POST /chat/start
{ "caseId": "7dc1b3d6-6ead-3667-82fa-4e3cf1b114a2" }

200 OK
{ "contactId": "…", "participantId": "…", "participantToken": "…", "region": "us-west-2" }`}</Code>
              </Endpoint>
              <Endpoint method="POST" path="/screenshare/start">
                <p>
                  Opt-in. Start a web call with screen sharing for one of your cases; returns the
                  participant token plus the Amazon Chime SDK <code>meeting</code> and{" "}
                  <code>attendee</code>.
                </p>
              </Endpoint>
              <Endpoint method="GET" path="/screenshare/status/{contactId}">
                <p>
                  Opt-in. <code>{`{ "state": "waiting" | "connected" | "ended" }`}</code> for your own
                  web call.
                </p>
              </Endpoint>
            </Section>

            <Section id="multi-tenancy" title="Multi-tenancy">
              <p>
                AnyCompanyPay is multi-tenant: many merchants share the platform, and a merchant must never see
                another's data. The tenant key <code>merchant_id</code> travels in the Cognito ID token and
                is enforced server-side everywhere:
              </p>
              <ul className="list-disc space-y-1 pl-5">
                <li>
                  <strong>Database:</strong> every query is scoped with{" "}
                  <code>WHERE merchant_id = &lt;token claim&gt;</code>.
                </li>
                <li>
                  <strong>Search:</strong> a shared OpenSearch index keyed by <code>merchant_id</code>, with
                  the tenant filter forced from the token (the "pool" isolation pattern).
                </li>
                <li>
                  <strong>Support:</strong> Cases, chats, web calls and after-hours follow-up tasks are
                  tagged with the tenant; cross-tenant access returns <code>403</code>/<code>404</code>.
                </li>
                <li>
                  <strong>AI agents:</strong> the AgentCore Gateway interceptor injects the trusted tenant
                  from the Connect contact; an external A2A partner only receives the contact it was
                  handed.
                </li>
              </ul>
            </Section>
          </div>

          <footer className="mt-16 border-t border-ink-100 pt-6 text-sm text-ink-400">
            AnyCompanyPay is a fictional platform for a demo/reference project. All endpoints and data are
            synthetic sample content. Not affiliated with any payment provider.
          </footer>
        </main>
      </div>
    </div>
  );
}
