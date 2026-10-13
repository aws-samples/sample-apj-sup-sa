import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/Button";

export default function AuthCallback() {
  const { handleRedirectCallback } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    handleRedirectCallback()
      .then((returnTo) => navigate(returnTo, { replace: true }))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Authentication failed")
      );
  }, [handleRedirectCallback, navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-ink-50 px-6">
      <div className="w-full max-w-sm rounded-2xl border border-ink-200 bg-white p-8 text-center shadow-card">
        {error ? (
          <>
            <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-red-50 text-red-600">
              !
            </div>
            <h1 className="mt-4 text-base font-semibold text-ink-900">
              Sign-in failed
            </h1>
            <p className="mt-1 text-sm text-ink-500">{error}</p>
            <Button
              variant="primary"
              size="md"
              className="mt-5 w-full justify-center"
              onClick={() => navigate("/", { replace: true })}
            >
              Back to home
            </Button>
          </>
        ) : (
          <>
            <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-ink-200 border-t-brand-600" />
            <p className="mt-4 text-sm text-ink-500">Completing sign-in…</p>
          </>
        )}
      </div>
    </div>
  );
}
