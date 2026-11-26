"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { LogIn, LogOut } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { useAuth } from "@/lib/auth";
import dialogStyles from "@/components/common/common.module.css";
import formStyles from "@/features/pages.module.css";

// Topbar auth control: "Sign in" (opens a username/password dialog) when logged out, or
// "Signed in as <email> · Sign out" when logged in. Admins read+write; viewers read only.
// Whether viewing needs login depends on the deployment toggle (CONSOLE_REQUIRE_AUTH_FOR_READS):
// when reads are public, logging in is only needed to perform admin writes.
export function SignInControl() {
  const { loggedIn, email, ready, login, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (loggedIn) {
    return (
      <div className={formStyles.actionsStart}>
        <span className={formStyles.subtle}>Signed in as {email}</span>
        <Button size="sm" variant="ghost" onClick={logout}>
          <LogOut size={15} />
          Sign out
        </Button>
      </div>
    );
  }

  async function submit() {
    if (!username.trim() || !password) return;
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password);
      setOpen(false);
      setUsername("");
      setPassword("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setError("");
        }
      }}
    >
      <Dialog.Trigger asChild>
        <Button size="sm" variant="secondary" disabled={!ready} title={ready ? undefined : "Loading console config…"}>
          <LogIn size={15} />
          Sign in
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className={dialogStyles.dialogOverlay} />
          <Dialog.Content className={dialogStyles.dialogContent}>
          <Dialog.Title className={dialogStyles.dialogTitle}>Sign in</Dialog.Title>
          <Dialog.Description className={dialogStyles.dialogDescription}>
            This console is restricted to authorized users. Sign in to view and manage usage.
          </Dialog.Description>

          {error ? <div className={formStyles.errorNotice}>{error}</div> : null}

          <div className={formStyles.field} style={{ marginTop: 8 }}>
            <label htmlFor="auth-username">Username</label>
            <Input
              id="auth-username"
              value={username}
              autoComplete="username"
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div className={formStyles.field} style={{ marginTop: 10 }}>
            <label htmlFor="auth-password">Password</label>
            <Input
              id="auth-password"
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
            />
          </div>

          <div className={dialogStyles.dialogActions}>
            <Dialog.Close asChild>
              <Button variant="secondary">Cancel</Button>
            </Dialog.Close>
            <Button
              variant="primary"
              onClick={submit}
              disabled={!username.trim() || !password || busy}
            >
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
