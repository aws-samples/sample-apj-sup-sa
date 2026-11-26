"use client";

import {
  BarChart3,
  Gauge,
  LineChart,
  ScrollText,
  ShieldAlert,
  SlidersHorizontal,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { PropsWithChildren } from "react";
import { cn } from "@/lib/utils";
import { SignInControl } from "@/components/common/SignInControl";
import { useAuth } from "@/lib/auth";
import { useConsoleConfigState } from "@/lib/config";
import styles from "@/components/layout/AppShell.module.css";

const navItems = [
  { href: "/", label: "Overview", icon: Gauge },
  { href: "/users", label: "Users", icon: Users },
  { href: "/statistics", label: "Statistics", icon: LineChart },
  { href: "/limits", label: "Limits", icon: SlidersHorizontal },
  { href: "/blocked", label: "Blocked", icon: ShieldAlert },
  { href: "/audit", label: "Audit", icon: ScrollText },
];

export function AppShell({ children }: PropsWithChildren) {
  const pathname = usePathname();
  const { loggedIn } = useAuth();
  const configState = useConsoleConfigState();
  const configReady = configState.status === "ready";
  // Default to requiring auth until config resolves (fail-closed: don't flash protected content).
  const requireAuthForReads = configState.status === "ready" ? configState.config.requireAuthForReads : true;
  // Show the global sign-in gate while config loads, or when reads require auth and we're not
  // logged in. In anonymous-read mode (requireAuthForReads=false) pages render without login;
  // write controls still disable for non-admins (canWrite).
  const showGate = !configReady || (requireAuthForReads && !loggedIn);

  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <div className={styles.brandMark}>
            <BarChart3 aria-hidden size={18} />
          </div>
          <div>
            <div className={styles.brandTitle}>Bedrock Meter</div>
            <div className={styles.brandSubtitle}>Console</div>
          </div>
        </div>

        <nav className={styles.nav} aria-label="Primary navigation">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link key={item.href} href={item.href} className={cn(styles.navItem, active && styles.navItemActive)}>
                <Icon size={17} aria-hidden />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </aside>

      <div className={styles.mainColumn}>
        <header className={styles.topbar}>
          <div>
            <div className={styles.environment}>Production</div>
            <h1 className={styles.topbarTitle}>Usage Metering</h1>
          </div>
          <SignInControl />
        </header>
        <main className={styles.content}>
          {!showGate ? (
            children
          ) : (
            <div className={styles.authGate}>
              <h2>Sign in required</h2>
              <p>
                {configReady
                  ? "This console is restricted to authorized users. Use “Sign in” (top right) to continue."
                  : "Loading console configuration…"}
              </p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
