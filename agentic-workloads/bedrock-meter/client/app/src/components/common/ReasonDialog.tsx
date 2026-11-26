"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import styles from "@/components/common/common.module.css";

export function ReasonDialog({
  title,
  description,
  confirmLabel,
  triggerLabel,
  danger,
  triggerVariant,
  triggerDisabled,
  onConfirm,
}: {
  title: string;
  description: string;
  confirmLabel: string;
  // Defaults to confirmLabel + "…" so the trigger reads as "open dialog" and
  // the in-dialog primary button reads as the final action.
  triggerLabel?: string;
  danger?: boolean;
  triggerVariant?: "primary" | "secondary" | "ghost" | "danger";
  triggerDisabled?: boolean;
  onConfirm: (reason: string) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function submit() {
    if (!reason.trim()) return;
    setIsSubmitting(true);
    try {
      await onConfirm(reason.trim());
      setReason("");
      setOpen(false);
    } finally {
      setIsSubmitting(false);
    }
  }

  const resolvedTriggerLabel = triggerLabel ?? `${confirmLabel}…`;

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button variant={triggerVariant ?? (danger ? "danger" : "primary")} size="sm" disabled={triggerDisabled}>
          {resolvedTriggerLabel}
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.dialogOverlay} />
        <Dialog.Content className={styles.dialogContent}>
          <Dialog.Title className={styles.dialogTitle}>{title}</Dialog.Title>
          <Dialog.Description className={styles.dialogDescription}>{description}</Dialog.Description>
          <label className={styles.reasonLabel} htmlFor="reason">
            Reason
          </label>
          <Textarea id="reason" value={reason} onChange={(event) => setReason(event.target.value)} />
          <div className={styles.dialogActions}>
            <Dialog.Close asChild>
              <Button variant="secondary">Cancel</Button>
            </Dialog.Close>
            <Button variant={danger ? "danger" : "primary"} onClick={submit} disabled={!reason.trim() || isSubmitting}>
              Confirm
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
