import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import type { PersonaMeta } from "./navConfig";
import { cn } from "@/lib/cn";
import { loadConnectConfig } from "@/connect/config";

export function Sidebar({ meta }: { meta: PersonaMeta }) {
  // Connect-dependent nav items (Cases, Support, Contact Center) only show once
  // the connect module is deployed (runtime config carries Connect settings).
  const [connectEnabled, setConnectEnabled] = useState(false);
  const [searchEnabled, setSearchEnabled] = useState(false);
  useEffect(() => {
    let alive = true;
    void loadConnectConfig().then((cfg) => {
      if (!alive) return;
      setConnectEnabled(!!cfg?.ccpUrl || !!cfg?.casesApiUrl);
      setSearchEnabled(!!cfg?.searchApiUrl);
    });
    return () => {
      alive = false;
    };
  }, []);

  const navItems = meta.nav.filter(
    (item) =>
      (!item.requiresConnect || connectEnabled) && (!item.requiresSearch || searchEnabled)
  );

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-ink-200 bg-white lg:flex">
      <div className="flex h-16 items-center gap-2.5 px-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <path
              d="M6 19V5h6.5c2.9 0 4.9 1.7 4.9 4.4S15.4 13.8 12.5 13.8H9.4V19H6z"
              fill="currentColor"
            />
          </svg>
        </div>
        <div className="leading-tight">
          <div className="text-sm font-bold text-ink-900">{meta.productName}</div>
          <div className="text-[11px] font-medium text-ink-400">
            {meta.roleLabel}
          </div>
        </div>
      </div>

      <nav className="flex-1 space-y-0.5 px-3 py-3">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              cn(
                "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-brand-50 text-brand-700"
                  : "text-ink-600 hover:bg-ink-50 hover:text-ink-900"
              )
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className={cn(
                    isActive ? "text-brand-600" : "text-ink-400 group-hover:text-ink-600"
                  )}
                >
                  {item.icon}
                </span>
                <span className="flex-1">{item.label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-ink-100 p-3">
        <div className="rounded-lg bg-ink-50 p-3">
          <p className="text-xs font-semibold text-ink-700">
            {meta.key === "merchant" ? "Test mode" : "Sandbox data"}
          </p>
          <p className="mt-0.5 text-[11px] leading-snug text-ink-400">
            {meta.key === "merchant"
              ? "You're viewing sample transactions."
              : "Platform metrics are simulated."}
          </p>
        </div>
      </div>
    </aside>
  );
}
