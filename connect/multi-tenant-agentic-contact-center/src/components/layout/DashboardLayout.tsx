import { NavLink, Outlet } from "react-router-dom";
import type { Persona } from "@/data/types";
import { personaConfig } from "./navConfig";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { FloatingChat } from "@/components/chat/FloatingChat";
import { cn } from "@/lib/cn";

export function DashboardLayout({ persona }: { persona: Persona }) {
  const meta = personaConfig[persona];

  return (
    <div className="flex h-screen overflow-hidden bg-ink-50">
      <Sidebar meta={meta} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar meta={meta} />

        {/* Mobile nav */}
        <div className="flex gap-1 overflow-x-auto border-b border-ink-200 bg-white px-3 py-2 scrollbar-thin lg:hidden">
          {meta.nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium",
                  isActive
                    ? "bg-brand-50 text-brand-700"
                    : "text-ink-500 hover:bg-ink-50"
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </div>

        <main className="flex-1 overflow-y-auto scrollbar-thin">
          <div className="mx-auto max-w-7xl px-4 py-6 lg:px-8 lg:py-8">
            <Outlet />
          </div>
        </main>
      </div>

      {/* Floating live-chat widget (merchant workspace only). Uses ChatJS, which
          shares window.connect with amazon-connect-streams; Streams only loads on
          the admin Contact Center page, so restricting this to merchants avoids
          any global clash. */}
      {persona === "merchant" && <FloatingChat />}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-ink-900">
          {title}
        </h1>
        {description && (
          <p className="mt-1 text-sm text-ink-500">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
