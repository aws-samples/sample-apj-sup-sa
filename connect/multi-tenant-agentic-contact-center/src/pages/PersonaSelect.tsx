import { Link } from "react-router-dom";
import { StoreIcon, ShieldIcon, ArrowUpRight } from "@/components/ui/icons";

export default function PersonaSelect() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-white to-ink-50">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path
                d="M6 19V5h6.5c2.9 0 4.9 1.7 4.9 4.4S15.4 13.8 12.5 13.8H9.4V19H6z"
                fill="currentColor"
              />
            </svg>
          </div>
          <span className="text-lg font-bold text-ink-900">AnyCompanyPay</span>
        </div>
        <div className="flex items-center gap-4">
          <Link to="/docs" className="text-sm font-medium text-ink-500 hover:text-ink-900">
            Developer docs
          </Link>
          <span className="rounded-full bg-ink-100 px-3 py-1 text-xs font-medium text-ink-500">
            Demo environment
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 pb-20 pt-8">
        <div className="mx-auto max-w-2xl text-center">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-3 py-1 text-xs font-semibold text-brand-700 ring-1 ring-inset ring-brand-600/20">
            Amazon Connect and AgentCore samples
          </span>
          <h1 className="mt-4 text-4xl font-extrabold tracking-tight text-ink-900 sm:text-5xl">
            Agentic customer support, end to end
          </h1>
          <p className="mt-4 text-base text-ink-500">
            A sample app on Amazon Connect: live chat, Q in Connect agentic
            self-service backed by a Bedrock AgentCore Gateway MCP tool, and
            support cases — with strict per-merchant tenant isolation. Pick a
            workspace to explore.
          </p>
        </div>

        <div className="mt-12 grid gap-6 md:grid-cols-2">
          <PersonaCard
            to="/merchant"
            accent="#635bff"
            icon={<StoreIcon className="h-6 w-6" />}
            eyebrow="For customers (merchants)"
            title="Merchant workspace"
            description="Sign in as a merchant to chat with the AI assistant about your own transactions, search your payments, and raise support cases — everything scoped to your tenant."
            highlights={[
              "AI live chat (Q in Connect self-service)",
              "Tenant-isolated transaction Q&A",
              "Support cases + chat about a case",
            ]}
          />
          <PersonaCard
            to="/admin"
            accent="#0f172a"
            icon={<ShieldIcon className="h-6 w-6" />}
            eyebrow="For the support team"
            title="Admin / Agent workspace"
            description="Sign in as an agent to work the support queue in the Amazon Connect CCP, handle live chats, and manage cases across merchants."
            highlights={[
              "Amazon Connect agent CCP (chat)",
              "Cases queue + Customer Profiles",
              "Cross-tenant admin view",
            ]}
          />
        </div>
      </main>
    </div>
  );
}

function PersonaCard({
  to,
  accent,
  icon,
  eyebrow,
  title,
  description,
  highlights,
}: {
  to: string;
  accent: string;
  icon: React.ReactNode;
  eyebrow: string;
  title: string;
  description: string;
  highlights: string[];
}) {
  return (
    <Link
      to={to}
      className="group relative overflow-hidden rounded-2xl border border-ink-200 bg-white p-7 shadow-card transition-all hover:-translate-y-0.5 hover:shadow-elevated"
    >
      <div
        className="flex h-12 w-12 items-center justify-center rounded-xl text-white"
        style={{ backgroundColor: accent }}
      >
        {icon}
      </div>
      <p className="mt-5 text-xs font-semibold uppercase tracking-wide text-ink-400">
        {eyebrow}
      </p>
      <h2 className="mt-1 flex items-center gap-2 text-xl font-bold text-ink-900">
        {title}
        <ArrowUpRight className="h-5 w-5 text-ink-300 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-ink-500" />
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-ink-500">{description}</p>
      <ul className="mt-5 space-y-2">
        {highlights.map((h) => (
          <li key={h} className="flex items-center gap-2 text-sm text-ink-600">
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: accent }}
            />
            {h}
          </li>
        ))}
      </ul>
      <span className="mt-6 inline-flex items-center gap-1 text-sm font-semibold text-brand-600">
        Enter workspace
      </span>
    </Link>
  );
}
