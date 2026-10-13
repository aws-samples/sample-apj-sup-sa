import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { PersonaMeta } from "./navConfig";
import { BellIcon, ChevronDown, LogOutIcon } from "@/components/ui/icons";
import { Avatar } from "@/components/ui/Avatar";
import { useAuth } from "@/auth/AuthProvider";
import { cn } from "@/lib/cn";

export function Topbar({ meta }: { meta: PersonaMeta }) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);

  const isMerchant = meta.key === "merchant";
  const displayName = user?.email
    ? user.email.split("@")[0]
    : isMerchant
    ? "Merchant"
    : "Agent";
  const displaySub = user?.email ?? (isMerchant ? "Merchant" : "Operations");
  const avatarColor = isMerchant ? "#635bff" : "#0f172a";
  // Tenant identity comes from the JWT (custom:merchant_name), not mock data.
  const tenantName = isMerchant ? user?.merchantName : undefined;

  const otherPersona = isMerchant
    ? { label: "Switch to Admin", to: "/admin" }
    : { label: "Switch to Merchant", to: "/merchant" };

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-4 border-b border-ink-200 bg-white/80 px-4 backdrop-blur lg:px-6">
      <Link
        to="/"
        className="flex items-center gap-2 text-sm font-bold text-ink-900 lg:hidden"
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-white">
          P
        </span>
      </Link>

      <div className="flex-1" />

      <div className="flex items-center gap-1.5">
        <span
          className={cn(
            "hidden rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset sm:inline-flex",
            isMerchant
              ? "bg-brand-50 text-brand-700 ring-brand-600/20"
              : "bg-ink-900 text-white ring-ink-900/20"
          )}
        >
          {isMerchant ? "Merchant" : "Admin / Agent"}
        </span>

        {tenantName && (
          <span
            className="hidden items-center gap-1.5 rounded-full bg-ink-100 px-2.5 py-1 text-xs font-semibold text-ink-700 ring-1 ring-inset ring-ink-500/20 sm:inline-flex"
            title="Tenant from your sign-in token"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            {tenantName}
          </span>
        )}

        <button className="relative flex h-9 w-9 items-center justify-center rounded-lg text-ink-500 hover:bg-ink-100">
          <BellIcon className="h-5 w-5" />
          <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-red-500" />
        </button>

        <button
          onClick={() => void logout()}
          title="Sign out"
          aria-label="Sign out"
          className="flex h-9 items-center gap-1.5 rounded-lg px-2.5 text-sm font-medium text-ink-500 hover:bg-red-50 hover:text-red-600"
        >
          <LogOutIcon className="h-4 w-4" />
          <span className="hidden sm:inline">Sign out</span>
        </button>

        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="flex items-center gap-2 rounded-lg py-1 pl-1 pr-2 hover:bg-ink-100"
          >
            <Avatar name={displayName} size={32} color={avatarColor} />
            <div className="hidden text-left leading-tight md:block">
              <div className="text-xs font-semibold text-ink-900">
                {displayName}
              </div>
              <div className="text-[11px] text-ink-400">{displaySub}</div>
            </div>
            <ChevronDown className="hidden h-4 w-4 text-ink-400 md:block" />
          </button>

          {menuOpen && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setMenuOpen(false)}
              />
              <div className="absolute right-0 z-20 mt-2 w-56 animate-fade-in rounded-xl border border-ink-200 bg-white p-1.5 shadow-elevated">
                <div className="px-3 py-2">
                  <div className="text-sm font-semibold text-ink-900">
                    {displayName}
                  </div>
                  <div className="text-xs text-ink-400">{displaySub}</div>
                  {tenantName && (
                    <div className="mt-1 text-[11px] font-medium text-brand-600">
                      {tenantName} · {user?.merchantId}
                    </div>
                  )}
                </div>
                <div className="my-1 h-px bg-ink-100" />
                <button
                  onClick={() => {
                    setMenuOpen(false);
                    navigate(otherPersona.to);
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-ink-700 hover:bg-ink-50"
                >
                  {otherPersona.label}
                </button>
                <button
                  onClick={() => {
                    setMenuOpen(false);
                    void logout();
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-red-600 hover:bg-red-50"
                >
                  <LogOutIcon className="h-4 w-4" />
                  Sign out
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
