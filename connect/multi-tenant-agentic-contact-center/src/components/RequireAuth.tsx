import { type ReactNode } from "react";
import { useLocation, Link } from "react-router-dom";
import type { Persona } from "@/data/types";
import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/Button";
import { StoreIcon, ShieldIcon, LogOutIcon } from "@/components/ui/icons";

const personaLabels: Record<Persona, { title: string; blurb: string }> = {
  merchant: {
    title: "Merchant Dashboard",
    blurb: "Sign in to manage your revenue, payments, and customers.",
  },
  admin: {
    title: "Admin / Agent Dashboard",
    blurb: "Sign in to oversee merchants, disputes, and platform risk.",
  },
};

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-b from-white to-ink-50 px-6">
      <div className="w-full max-w-sm rounded-2xl border border-ink-200 bg-white p-8 text-center shadow-card">
        {children}
      </div>
    </div>
  );
}

function PersonaIcon({ persona }: { persona: Persona }) {
  const accent = persona === "merchant" ? "#635bff" : "#0f172a";
  return (
    <div
      className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl text-white"
      style={{ backgroundColor: accent }}
    >
      {persona === "merchant" ? (
        <StoreIcon className="h-6 w-6" />
      ) : (
        <ShieldIcon className="h-6 w-6" />
      )}
    </div>
  );
}

function LoginPrompt({
  persona,
  onLogin,
}: {
  persona: Persona;
  onLogin: () => void;
}) {
  const meta = personaLabels[persona];
  return (
    <Shell>
      <PersonaIcon persona={persona} />
      <h1 className="mt-5 text-lg font-bold text-ink-900">{meta.title}</h1>
      <p className="mt-1.5 text-sm text-ink-500">{meta.blurb}</p>
      <Button
        variant="primary"
        size="md"
        className="mt-6 w-full justify-center"
        onClick={onLogin}
      >
        Sign in to continue
      </Button>
      <Link
        to="/"
        className="mt-3 inline-block text-sm font-medium text-ink-500 hover:text-ink-700"
      >
        Back to home
      </Link>
    </Shell>
  );
}

function NotAuthorized({ persona }: { persona: Persona }) {
  const { user, logout } = useAuth();
  const meta = personaLabels[persona];
  const otherPersona: Persona = persona === "merchant" ? "admin" : "merchant";
  const inOther = user?.groups?.includes(otherPersona);

  return (
    <Shell>
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-amber-50 text-amber-600">
        <ShieldIcon className="h-6 w-6" />
      </div>
      <h1 className="mt-5 text-lg font-bold text-ink-900">Access restricted</h1>
      <p className="mt-1.5 text-sm text-ink-500">
        {user?.email ? <span className="font-medium">{user.email}</span> : "This account"}{" "}
        isn&apos;t allowed in the {meta.title.toLowerCase()}.
      </p>
      {inOther && (
        <Link to={`/${otherPersona}`} className="mt-5 block">
          <Button variant="primary" size="md" className="w-full justify-center">
            Go to your {otherPersona} workspace
          </Button>
        </Link>
      )}
      <Button
        variant="secondary"
        size="md"
        className="mt-3 w-full justify-center"
        onClick={() => void logout()}
      >
        <LogOutIcon className="h-4 w-4" />
        Sign out
      </Button>
    </Shell>
  );
}

/**
 * Route guard for a persona workspace. Renders children only when the user is
 * authenticated AND belongs to the matching Cognito group. Otherwise shows a
 * login prompt (unauthenticated) or an access-restricted screen (wrong group).
 */
export function RequireAuth({
  persona,
  children,
}: {
  persona: Persona;
  children: ReactNode;
}) {
  const { isLoading, isAuthenticated, user, login } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <Shell>
        <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-ink-200 border-t-brand-600" />
        <p className="mt-4 text-sm text-ink-500">Loading…</p>
      </Shell>
    );
  }

  if (!isAuthenticated) {
    return (
      <LoginPrompt
        persona={persona}
        onLogin={() => void login(location.pathname)}
      />
    );
  }

  if (!user?.groups?.includes(persona)) {
    return <NotAuthorized persona={persona} />;
  }

  return <>{children}</>;
}
