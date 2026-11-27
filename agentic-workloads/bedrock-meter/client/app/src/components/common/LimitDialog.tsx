"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Field";
import { getErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { limitHierarchyErrors } from "@/lib/limit-validation";
import { useSaveUserLimits, useUserLimit } from "@/lib/queries";
import type { LimitOverride } from "@/lib/types";
import dialogStyles from "@/components/common/common.module.css";
import formStyles from "@/features/pages.module.css";

const TOKENS_PER_K = 1000;

// Console edits limits in thousands of credits (k); the backend stores raw credits.
function tokensToK(raw: number): number {
  return raw / TOKENS_PER_K;
}
function kToTokens(k: number): number {
  return Math.round(k * TOKENS_PER_K);
}

type TokenField = "dailyTokenLimit" | "weeklyTokenLimit" | "monthlyTokenLimit";

function limitChanges(draft: LimitOverride): Omit<LimitOverride, "username"> {
  return {
    dailyTokenLimit: draft.dailyTokenLimit,
    weeklyTokenLimit: draft.weeklyTokenLimit,
    monthlyTokenLimit: draft.monthlyTokenLimit,
    warningThresholdPct: draft.warningThresholdPct,
    exempt: draft.exempt,
  };
}

export function LimitDialog({
  username,
  triggerLabel = "Limit",
  triggerVariant = "secondary",
  onSaved,
}: {
  username: string;
  triggerLabel?: string;
  triggerVariant?: "primary" | "secondary" | "ghost" | "danger";
  onSaved?: (username: string) => void;
}) {
  const { canWrite } = useAuth();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<LimitOverride | null>(null);
  const [reason, setReason] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);

  // Only fetch the effective limit while the dialog is open.
  const { data: loadedLimit, error, isFetching } = useUserLimit(username, open);
  const saveLimit = useSaveUserLimits();

  useEffect(() => {
    if (loadedLimit) setDraft(loadedLimit);
  }, [loadedLimit]);

  // Reset transient state each time the dialog closes.
  useEffect(() => {
    if (!open) {
      setDraft(null);
      setReason("");
      setSubmitError(null);
    }
  }, [open]);

  function updateTokenK(field: TokenField, value: string) {
    const k = Number(value);
    const raw = Number.isFinite(k) ? kToTokens(k) : 0;
    setDraft((current) => (current ? { ...current, [field]: raw } : current));
  }

  function updateThresholdPct(value: string) {
    const numeric = Number(value);
    setDraft((current) =>
      current ? { ...current, warningThresholdPct: Number.isFinite(numeric) ? numeric : 0 } : current,
    );
  }

  const validationErrors = draft ? limitHierarchyErrors(draft) : [];
  const hasValidationError = validationErrors.length > 0;

  async function submit() {
    if (!draft || !reason.trim() || hasValidationError || !canWrite) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      await saveLimit.mutateAsync({ username, changes: limitChanges(draft), reason: reason.trim() });
      onSaved?.(username);
      setOpen(false);
    } catch (saveError) {
      setSubmitError(saveError);
    } finally {
      setIsSubmitting(false);
    }
  }

  const ready = Boolean(draft) && !isFetching;

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button variant={triggerVariant} size="sm">
          <SlidersHorizontal size={15} />
          {triggerLabel}
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className={dialogStyles.dialogOverlay} />
        <Dialog.Content className={dialogStyles.dialogContent}>
          <Dialog.Title className={dialogStyles.dialogTitle}>Edit {username} limits</Dialog.Title>
          <Dialog.Description className={dialogStyles.dialogDescription}>
            Limits are credits (cost-weighted tokens), entered in k = 1,000. A reason is required.
          </Dialog.Description>

          {error || submitError ? (
            <div className={formStyles.errorNotice} role="alert">
              {getErrorMessage(submitError ?? error)}
            </div>
          ) : null}

          {!ready ? (
            <div className={formStyles.empty}>Loading current limits…</div>
          ) : (
            <>
              <div className={formStyles.formGrid}>
                <div className={formStyles.field}>
                  <label htmlFor="ld-daily">Daily (k credits)</label>
                  <Input
                    id="ld-daily"
                    min={0}
                    type="number"
                    value={draft ? tokensToK(draft.dailyTokenLimit) : ""}
                    onChange={(event) => updateTokenK("dailyTokenLimit", event.target.value)}
                  />
                </div>
                <div className={formStyles.field}>
                  <label htmlFor="ld-weekly">Weekly (k credits)</label>
                  <Input
                    id="ld-weekly"
                    min={0}
                    type="number"
                    value={draft ? tokensToK(draft.weeklyTokenLimit) : ""}
                    onChange={(event) => updateTokenK("weeklyTokenLimit", event.target.value)}
                  />
                </div>
                <div className={formStyles.field}>
                  <label htmlFor="ld-monthly">Monthly (k credits)</label>
                  <Input
                    id="ld-monthly"
                    min={0}
                    type="number"
                    value={draft ? tokensToK(draft.monthlyTokenLimit) : ""}
                    onChange={(event) => updateTokenK("monthlyTokenLimit", event.target.value)}
                  />
                </div>
                <div className={formStyles.field}>
                  <label htmlFor="ld-warning">Warning threshold (%)</label>
                  <Input
                    id="ld-warning"
                    max={100}
                    min={1}
                    type="number"
                    value={draft?.warningThresholdPct ?? ""}
                    onChange={(event) => updateThresholdPct(event.target.value)}
                  />
                </div>
              </div>

              <label className={formStyles.checkboxLabel} style={{ marginTop: 12 }}>
                <input
                  checked={Boolean(draft?.exempt)}
                  type="checkbox"
                  onChange={(event) =>
                    setDraft((current) => (current ? { ...current, exempt: event.target.checked } : current))
                  }
                />
                User is exempt
              </label>

              {!canWrite ? (
                <div className={formStyles.errorNotice} role="alert" style={{ marginTop: 12 }}>
                  Changing limits requires the admin role.
                </div>
              ) : null}

              {hasValidationError ? (
                <div className={formStyles.errorNotice} role="alert" style={{ marginTop: 12 }}>
                  {validationErrors.map((message) => (
                    <div key={message}>⚠ {message}</div>
                  ))}
                </div>
              ) : null}

              <label className={dialogStyles.reasonLabel} htmlFor="ld-reason" style={{ marginTop: 14 }}>
                Reason
              </label>
              <Textarea id="ld-reason" value={reason} onChange={(event) => setReason(event.target.value)} />
            </>
          )}

          <div className={dialogStyles.dialogActions}>
            <Dialog.Close asChild>
              <Button variant="secondary">Cancel</Button>
            </Dialog.Close>
            <Button
              variant="primary"
              onClick={submit}
              disabled={!ready || !reason.trim() || hasValidationError || !canWrite || isSubmitting}
            >
              Save changes
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
