"use client";

import { CalendarClock, Save, ShieldOff } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ReasonDialog } from "@/components/common/ReasonDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input, Select } from "@/components/ui/Field";
import { getErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fullNumber } from "@/lib/format";
import { limitHierarchyErrors } from "@/lib/limit-validation";
import {
  useDeleteUserLimit,
  useLimits,
  useSaveQuotaTimeZone,
  useSaveUserLimits,
  useUserLimit,
} from "@/lib/queries";
import type { LimitOverride } from "@/lib/types";
import styles from "@/features/pages.module.css";

type TokenField = "dailyTokenLimit" | "weeklyTokenLimit" | "monthlyTokenLimit";

const TOKENS_PER_K = 1000;
const COMMON_TIME_ZONES = [
  "UTC",
  "Asia/Seoul",
  "Asia/Tokyo",
  "Asia/Singapore",
  "America/Los_Angeles",
  "America/New_York",
  "Europe/London",
];

function supportedTimeZones(): string[] {
  const intl = Intl as typeof Intl & {
    supportedValuesOf?: (key: "timeZone") => string[];
  };
  return intl.supportedValuesOf?.("timeZone") ?? COMMON_TIME_ZONES;
}

// The console edits limits in thousands of tokens (k); the backend stores and
// enforces raw tokens. Convert only at the input/display boundary so save,
// known-users, and all backend payloads keep using raw token counts.
function tokensToK(raw: number): number {
  return raw / TOKENS_PER_K;
}

function kToTokens(k: number): number {
  return Math.round(k * TOKENS_PER_K);
}

function limitChanges(draft: LimitOverride): Omit<LimitOverride, "username"> {
  return {
    dailyTokenLimit: draft.dailyTokenLimit,
    weeklyTokenLimit: draft.weeklyTokenLimit,
    monthlyTokenLimit: draft.monthlyTokenLimit,
    warningThresholdPct: draft.warningThresholdPct,
    exempt: draft.exempt,
  };
}

export function LimitsPageClient() {
  const searchParams = useSearchParams();
  const requestedUser = searchParams.get("user") || "";
  const [targetInput, setTargetInput] = useState(requestedUser);
  const [loadedUsername, setLoadedUsername] = useState(requestedUser);
  const [draft, setDraft] = useState<LimitOverride | null>(null);
  const [quotaTimeZoneDraft, setQuotaTimeZoneDraft] = useState("");
  const [notice, setNotice] = useState("");

  const { data, error, isLoading } = useLimits();
  const {
    data: loadedLimit,
    error: limitError,
    isFetching: isLoadingLimit,
  } = useUserLimit(loadedUsername, Boolean(loadedUsername));
  const saveLimit = useSaveUserLimits();
  const deleteLimit = useDeleteUserLimit();
  const saveQuotaTimeZone = useSaveQuotaTimeZone();
  const { canWrite } = useAuth();

  useEffect(() => {
    if (requestedUser) {
      setTargetInput(requestedUser);
      setLoadedUsername(requestedUser);
    }
  }, [requestedUser]);

  useEffect(() => {
    if (!loadedUsername && data?.current.username) {
      setTargetInput(data.current.username);
      setLoadedUsername(data.current.username);
    }
  }, [data, loadedUsername]);

  useEffect(() => {
    if (loadedLimit) {
      setDraft(loadedLimit);
    }
  }, [loadedLimit]);

  useEffect(() => {
    if (data?.globalPolicy.quotaTimeZone) {
      setQuotaTimeZoneDraft(data.globalPolicy.quotaTimeZone);
    }
  }, [data?.globalPolicy.quotaTimeZone]);

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

  function loadTargetUser() {
    const username = targetInput.trim();
    if (!username) return;
    setLoadedUsername(username);
  }

  const validationErrors = draft ? limitHierarchyErrors(draft) : [];
  const hasValidationError = validationErrors.length > 0;
  const browserTimeZone =
    typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "";
  const timeZoneOptions = Array.from(new Set([
    data?.globalPolicy.quotaTimeZone,
    quotaTimeZoneDraft,
    browserTimeZone,
    ...COMMON_TIME_ZONES,
    ...supportedTimeZones(),
  ].filter(Boolean) as string[]));

  if (isLoading) {
    return <div className={styles.empty}>Loading limits...</div>;
  }

  if (error) {
    return <div className={styles.empty}>{getErrorMessage(error)}</div>;
  }

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div>
          <h2 className={styles.pageTitle}>Limits</h2>
          <p className={styles.pageDescription}>
            Manage effective user quota values through the deployed Bedrock Meter API.
          </p>
        </div>
        <Badge tone="blue">Live backend</Badge>
      </div>

      {notice ? <div className={styles.notice}>{notice}</div> : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Global Quota Calendar</CardTitle>
            <CardDescription>
              Day, ISO week, and calendar month boundaries use one IANA time zone for every principal.
            </CardDescription>
          </div>
          <CalendarClock size={18} aria-hidden />
        </CardHeader>
        <CardContent>
          <div className={styles.formGrid}>
            <div className={styles.field}>
              <label htmlFor="quotaTimeZone">Quota time zone</label>
              <Select
                id="quotaTimeZone"
                value={quotaTimeZoneDraft}
                onChange={(event) => setQuotaTimeZoneDraft(event.target.value)}
              >
                {timeZoneOptions.map((timeZone) => (
                  <option key={timeZone} value={timeZone}>{timeZone}</option>
                ))}
              </Select>
            </div>
            <div className={styles.field}>
              <label>Policy mode</label>
              <Input readOnly value="Calendar day / ISO week / calendar month" />
            </div>
          </div>

          <div className={styles.formulaBox} style={{ marginTop: 16 }}>
            Changing the time zone starts a new quota counter namespace so UTC and local-day usage
            cannot mix. Current day, week, and month counters restart; previously blocked users are
            re-evaluated by the periodic check within five minutes.
          </div>

          <div className={styles.formFooter}>
            <span className={styles.subtle}>
              Active policy version: {data?.globalPolicy.quotaTimeZoneVersion ?? "—"}
            </span>
            <ReasonDialog
              title={`Change quota timezone to ${quotaTimeZoneDraft}`}
              description="This restarts active calendar quota counters. A reason is required."
              confirmLabel="Change timezone"
              triggerDisabled={
                !canWrite
                || !quotaTimeZoneDraft
                || quotaTimeZoneDraft === data?.globalPolicy.quotaTimeZone
                || saveQuotaTimeZone.isPending
              }
              onConfirm={async (reason) => {
                await saveQuotaTimeZone.mutateAsync({ quotaTimeZone: quotaTimeZoneDraft, reason });
                setNotice(`Global quota timezone changed to ${quotaTimeZoneDraft}.`);
              }}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Selected User Policy</CardTitle>
            <CardDescription>Load a path-less IAM username and adjust its effective quota override.</CardDescription>
          </div>
          <Save size={18} aria-hidden />
        </CardHeader>
        <CardContent>
          <div className={styles.formulaBox}>
            <strong>Limits are measured in credits</strong> — cost-weighted tokens, not a raw token count.
            <code className={styles.formula}>
              credits = raw input ×1 + cache read ×0.1 + cache write ×1.25 + output ×5
            </code>
            1 credit = 1 raw input token. Inputs are entered in <strong>k</strong> (thousands): 1k = 1,000 credits.
          </div>

          <div className={styles.toolbar}>
            <div className={styles.filters}>
              <Input
                aria-label="IAM username"
                placeholder="IAM username"
                value={targetInput}
                onChange={(event) => setTargetInput(event.target.value)}
              />
              <Button variant="secondary" onClick={loadTargetUser} disabled={!targetInput.trim() || isLoadingLimit}>
                Load
              </Button>
            </div>
          </div>

          {limitError ? <div className={styles.errorNotice}>{getErrorMessage(limitError)}</div> : null}

          <div className={styles.formGrid} style={{ marginTop: 16 }}>
            <div className={styles.field}>
              <label htmlFor="dailyTokenLimit">Daily (k credits)</label>
              <Input
                id="dailyTokenLimit"
                min={0}
                type="number"
                value={draft ? tokensToK(draft.dailyTokenLimit) : ""}
                onChange={(event) => updateTokenK("dailyTokenLimit", event.target.value)}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="weeklyTokenLimit">Weekly (k credits)</label>
              <Input
                id="weeklyTokenLimit"
                min={0}
                type="number"
                value={draft ? tokensToK(draft.weeklyTokenLimit) : ""}
                onChange={(event) => updateTokenK("weeklyTokenLimit", event.target.value)}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="monthlyTokenLimit">Monthly (k credits)</label>
              <Input
                id="monthlyTokenLimit"
                min={0}
                type="number"
                value={draft ? tokensToK(draft.monthlyTokenLimit) : ""}
                onChange={(event) => updateTokenK("monthlyTokenLimit", event.target.value)}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="warningThresholdPct">Warning threshold (%)</label>
              <Input
                id="warningThresholdPct"
                max={100}
                min={1}
                type="number"
                value={draft?.warningThresholdPct ?? ""}
                onChange={(event) => updateThresholdPct(event.target.value)}
              />
            </div>
          </div>
          {!canWrite ? (
            <div className={styles.errorNotice} role="alert" style={{ marginTop: 12 }}>
              Changing limits requires the admin role.
            </div>
          ) : null}
          {hasValidationError ? (
            <div className={styles.errorNotice} role="alert" style={{ marginTop: 12 }}>
              {validationErrors.map((message) => (
                <div key={message}>⚠ {message}</div>
              ))}
            </div>
          ) : null}

          <div className={styles.formFooter}>
            <label className={styles.checkboxLabel}>
              <input
                checked={Boolean(draft?.exempt)}
                type="checkbox"
                onChange={(event) => setDraft((current) => (current ? { ...current, exempt: event.target.checked } : current))}
              />
              User is exempt
            </label>
            {draft ? (
              <ReasonDialog
                title={`Save ${draft.username} policy`}
                description="A reason is required for the admin change."
                confirmLabel="Save changes"
                triggerDisabled={hasValidationError || !canWrite}
                onConfirm={async (reason) => {
                  await saveLimit.mutateAsync({ username: draft.username, changes: limitChanges(draft), reason });
                  setNotice(`${draft.username} policy saved.`);
                }}
              />
            ) : (
              <Button disabled>Save changes</Button>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Known User Policies</CardTitle>
            <CardDescription>Users discovered from top usage, blocked state, and configured console usernames.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <div className={styles.tableWrap}>
            <table className="data-grid">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Daily (k credits)</th>
                  <th>Weekly (k credits)</th>
                  <th>Monthly (k credits)</th>
                  <th>Warning</th>
                  <th>Exempt</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data?.knownUsers.length ? (
                  data.knownUsers.map((override) => (
                    <tr key={override.username}>
                      <td>
                        <div className={styles.userCell}>
                          <span className={styles.userName}>{override.username}</span>
                          <span className={styles.subtle}>Effective config</span>
                        </div>
                      </td>
                      <td>{fullNumber(tokensToK(override.dailyTokenLimit))}</td>
                      <td>{fullNumber(tokensToK(override.weeklyTokenLimit))}</td>
                      <td>{fullNumber(tokensToK(override.monthlyTokenLimit))}</td>
                      <td>{override.warningThresholdPct}%</td>
                      <td>{override.exempt ? <Badge tone="green">Yes</Badge> : <Badge>Not exempt</Badge>}</td>
                      <td>
                        <div className={styles.actions}>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => {
                              setTargetInput(override.username);
                              setLoadedUsername(override.username);
                              setDraft(override);
                            }}
                          >
                            Edit
                          </Button>
                          <ReasonDialog
                            title={`Remove ${override.username} override`}
                            description="This deletes the per-user config item and falls back to the default policy."
                            confirmLabel="Remove"
                            triggerVariant="danger"
                            triggerDisabled={!canWrite}
                            danger
                            onConfirm={async (reason) => {
                              await deleteLimit.mutateAsync({ username: override.username, reason });
                              setNotice(`${override.username} override removed.`);
                            }}
                          />
                        </div>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7} className={styles.empty}>
                      No known users were returned by the backend.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Operational Guardrails</CardTitle>
            <CardDescription>Limit changes should be deliberate because they can trigger unblock behavior.</CardDescription>
          </div>
          <ShieldOff size={18} aria-hidden />
        </CardHeader>
        <CardContent>
          <div className={styles.guardrailGrid}>
            <div>
              <strong>Self-lockout</strong>
              <p>The console blocks changing your own limits/exempt (matched on your signed-in identity).</p>
            </div>
            <div>
              <strong>Exempt users</strong>
              <p>Usage is still tracked, but warning and blocking enforcement is skipped.</p>
            </div>
            <div>
              <strong>Audit reason</strong>
              <p>Write operations require a reason before the console calls the backend.</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
