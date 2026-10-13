// Shared helpers for the routing Lambdas (invoked from Amazon Connect flows).
import { ConnectCasesClient, ListFieldsCommand } from "@aws-sdk/client-connectcases";
import { CustomerProfilesClient, SearchProfilesCommand } from "@aws-sdk/client-customer-profiles";

export type Tier = "VIP" | "key" | "shared";

// Contact priority per tier (1 = highest; Connect default is 5). Design §6.3.
export const TIER_PRIORITY: Record<Tier, string> = { VIP: "1", key: "2", shared: "5" };

/** The parts of the Amazon Connect Lambda event we use. */
export interface ConnectEvent {
  Details?: {
    ContactData?: {
      ContactId?: string;
      InitialContactId?: string;
      Channel?: string;
      InstanceARN?: string;
      Attributes?: Record<string, string>;
    };
    Parameters?: Record<string, string>;
  };
}

export function contactData(event: ConnectEvent) {
  const cd = event?.Details?.ContactData ?? {};
  return {
    contactId: cd.ContactId ?? "",
    initialContactId: cd.InitialContactId || cd.ContactId || "",
    channel: (cd.Channel ?? "CHAT").toUpperCase(),
    instanceArn: cd.InstanceARN ?? "",
    attributes: cd.Attributes ?? {},
    params: event?.Details?.Parameters ?? {},
  };
}

export function normalizeTier(v: string | undefined): Tier | null {
  const t = (v ?? "").trim().toLowerCase();
  if (t === "vip") return "VIP";
  if (t === "key" || t === "key-account" || t === "key_account") return "key";
  if (t === "shared") return "shared";
  return null;
}

/**
 * Resolve the merchant's tier. Source of truth is the `tier` attribute on the
 * merchant's Customer Profiles ACCOUNT_PROFILE (AccountNumber = merchant_id);
 * falls back to the deploy-time default map, then "shared" (design §4.1 step 2:
 * "Not found: continue with tier = shared").
 */
export async function resolveTier(
  profiles: CustomerProfilesClient,
  domainName: string,
  merchantId: string,
  defaults: Record<string, string>
): Promise<{ tier: Tier; profileArn: string; source: string }> {
  if (merchantId && domainName) {
    try {
      const res = await profiles.send(
        new SearchProfilesCommand({ DomainName: domainName, KeyName: "_account", Values: [merchantId] })
      );
      const acct = (res.Items ?? []).find((p) => p.ProfileType === "ACCOUNT_PROFILE") ?? res.Items?.[0];
      const fromProfile = normalizeTier(acct?.Attributes?.tier);
      const profileArn = acct?.ProfileId
        ? `arn:aws:profile:${process.env.AWS_REGION}:${process.env.ACCOUNT_ID}:domains/${domainName}/profiles/${acct.ProfileId}`
        : "";
      if (fromProfile) return { tier: fromProfile, profileArn, source: "profile" };
      const fromDefault = normalizeTier(defaults[merchantId]);
      if (fromDefault) return { tier: fromDefault, profileArn, source: "default-map" };
      return { tier: "shared", profileArn, source: "fallback" };
    } catch (err) {
      console.warn("resolveTier: SearchProfiles failed; using defaults", err);
    }
  }
  const fromDefault = normalizeTier(defaults[merchantId]);
  return { tier: fromDefault ?? "shared", profileArn: "", source: fromDefault ? "default-map" : "fallback" };
}

// Cases field name -> fieldId, resolved once per warm container so nothing is
// hardcoded to generated ids (same approach as the commerce API).
let fieldCache: Record<string, string> | null = null;
export async function caseFields(cases: ConnectCasesClient, domainId: string): Promise<Record<string, string>> {
  if (fieldCache) return fieldCache;
  const out: Record<string, string> = {};
  let next: string | undefined;
  do {
    const r = await cases.send(new ListFieldsCommand({ domainId, maxResults: 100, nextToken: next }));
    for (const f of r.fields ?? []) if (f.name && f.fieldId) out[f.name] = f.fieldId;
    next = r.nextToken;
  } while (next);
  fieldCache = out;
  return out;
}

/** "arn:aws:connect:...:instance/<id>/agent/<userId>" -> "<userId>"; plain ids pass through. */
export function userIdFromArn(v: string): string {
  const s = (v ?? "").trim();
  if (!s) return "";
  return s.includes("/") ? s.split("/").pop() ?? "" : s;
}

export function parseJsonMap(raw: string | undefined): Record<string, string> {
  if (!raw) return {};
  try {
    const v = JSON.parse(raw);
    return v && typeof v === "object" ? (v as Record<string, string>) : {};
  } catch {
    return {};
  }
}
