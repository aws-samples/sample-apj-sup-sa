// ============================================================================
// AnyCompanyPay routing — CONTACT CONTEXT (Pattern A: case-owner reply routing)
// ============================================================================
// Invoked by the routed inbound chat flow (Invoke AWS Lambda, response
// validation = JSON) BEFORE hours are checked. Returns everything the flow needs
// to route the contact, so the flow itself stays static:
//
//   tier / routingPriority / tierQueueArn  -> working queue + contact priority
//   isCaseChat / caseOpen / hasOwner       -> which branch the flow takes
//   routeToOwner + RoutingCriteria         -> "Set routing criteria (dynamic)":
//       step 1 = preferred agent (the case owner) with an expiry; when it
//       expires the contact falls back to the longest-available agent in the
//       queue (built-in behaviour, design §2.3 / §4.3).
//
// Trust: merchant_id and case_id are contact attributes stamped SERVER-SIDE by
// ChatApiFn from the merchant's validated Cognito JWT (case_id only after the
// case's tenant was verified), so they are safe to route on.
//
// Every branch degrades to standard tier routing: a lookup failure never drops
// the contact.
// ----------------------------------------------------------------------------
import { ConnectClient, GetCurrentUserDataCommand } from "@aws-sdk/client-connect";
import {
  ConnectCasesClient,
  CreateRelatedItemCommand,
  GetCaseCommand,
} from "@aws-sdk/client-connectcases";
import { CustomerProfilesClient } from "@aws-sdk/client-customer-profiles";
import {
  ConnectEvent,
  TIER_PRIORITY,
  Tier,
  caseFields,
  contactData,
  parseJsonMap,
  resolveTier,
  userIdFromArn,
} from "../shared/common";

const REGION = process.env.AWS_REGION!;
const INSTANCE_ID = process.env.CONNECT_INSTANCE_ID!;
const CASES_DOMAIN_ID = process.env.CASES_DOMAIN_ID!;
const PROFILES_DOMAIN = process.env.PROFILES_DOMAIN_NAME || "";
const TIER_DEFAULTS = parseJsonMap(process.env.TIER_DEFAULTS);
const TIER_QUEUES = parseJsonMap(process.env.TIER_QUEUE_ARNS) as Record<Tier, string>;
// Preferred-agent step expiry per channel (design §4.3: chat 60 s, email 900 s).
const OWNER_EXPIRY = parseJsonMap(process.env.OWNER_EXPIRY_SECONDS);
// When false the owner is targeted even if offline, until the step expires
// (test A-02); when true an offline owner is skipped (test A-05).
const OWNER_PRESENCE_CHECK = (process.env.OWNER_PRESENCE_CHECK ?? "true") === "true";
// Case statuses (system `status` / custom `case_status`) that count as closed.
const CLOSED = new Set(["closed", "resolved"]);

const connect = new ConnectClient({ region: REGION });
const cases = new ConnectCasesClient({ region: REGION });
const profiles = new CustomerProfilesClient({ region: REGION });

interface CaseInfo {
  open: boolean;
  ownerUserId: string;
  merchantId: string;
}

async function readCase(caseId: string): Promise<CaseInfo | null> {
  const f = await caseFields(cases, CASES_DOMAIN_ID);
  const want = ["status", "assigned_user", f["case_status"], f["merchant_id"]].filter(Boolean) as string[];
  const res = await cases.send(
    new GetCaseCommand({ domainId: CASES_DOMAIN_ID, caseId, fields: want.map((id) => ({ id })) })
  );
  const val = (id: string | undefined) => {
    const v = (res.fields ?? []).find((x) => x.id === id)?.value;
    return (v?.stringValue ?? v?.userArnValue ?? "") as string;
  };
  const assigned = val("assigned_user");
  // Log the raw owner value once: its format (user ID vs ARN) is an open
  // question in the design (§10.1) — userIdFromArn handles both.
  console.log(`case ${caseId}: status=${val("status")} case_status=${val(f["case_status"])} assigned_user=${assigned}`);
  const statuses = [val("status"), val(f["case_status"])].map((s) => s.toLowerCase());
  return {
    open: !statuses.some((s) => CLOSED.has(s)),
    ownerUserId: userIdFromArn(assigned),
    merchantId: val(f["merchant_id"]),
  };
}

/** Owner presence: Available (routable) status. Custom/Offline count as not online. */
async function ownerOnline(userId: string, channel: string): Promise<boolean> {
  const res = await connect.send(
    new GetCurrentUserDataCommand({ InstanceId: INSTANCE_ID, Filters: { Agents: [userId] } })
  );
  const u = res.UserDataList?.[0];
  if (!u) return false; // not logged in
  const status = u.Status?.StatusName ?? "";
  const slots = (u.AvailableSlotsByChannel ?? {})[channel as "CHAT"] ?? 0;
  console.log(`owner ${userId}: status=${status} slots(${channel})=${slots}`);
  // Available with no free slot still counts as online: the contact waits for
  // the owner until the step expires (test A-03).
  return status === "Available";
}

async function linkContactToCase(caseId: string, instanceArn: string, contactId: string) {
  if (!instanceArn || !contactId) return;
  try {
    await cases.send(
      new CreateRelatedItemCommand({
        domainId: CASES_DOMAIN_ID,
        caseId,
        type: "Contact",
        content: { contact: { contactArn: `${instanceArn}/contact/${contactId}` } },
      })
    );
  } catch (err) {
    // Already linked / not linkable is not a routing failure.
    console.warn(`link contact ${contactId} -> case ${caseId} failed`, err);
  }
}

export const handler = async (event: ConnectEvent) => {
  const { contactId, channel, instanceArn, attributes } = contactData(event);
  const merchantId = attributes.merchant_id ?? "";
  const caseId = attributes.case_id ?? "";

  const { tier, source } = await resolveTier(profiles, PROFILES_DOMAIN, merchantId, TIER_DEFAULTS);
  const out: Record<string, unknown> = {
    tier,
    tierSource: source,
    tierPriority: TIER_PRIORITY[tier],
    routingPriority: TIER_PRIORITY[tier],
    tierQueueArn: TIER_QUEUES[tier] ?? TIER_QUEUES.shared ?? "",
    isCaseChat: caseId ? "true" : "false",
    caseId,
    caseOpen: "false",
    hasOwner: "false",
    ownerUserId: "",
    ownerOnline: "false",
    routeToOwner: "false",
  };

  if (caseId) {
    try {
      const info = await readCase(caseId);
      // Defense-in-depth: ChatApiFn already verified the case's tenant.
      if (info && (!merchantId || !info.merchantId || info.merchantId === merchantId)) {
        await linkContactToCase(caseId, instanceArn, contactId);
        out.caseOpen = String(info.open);
        out.hasOwner = String(Boolean(info.ownerUserId));
        out.ownerUserId = info.ownerUserId;
        if (info.open && info.ownerUserId) {
          // A reply on an active case jumps ahead of new contacts (design G2).
          out.routingPriority = "1";
          const online = OWNER_PRESENCE_CHECK ? await ownerOnline(info.ownerUserId, channel) : true;
          out.ownerOnline = String(online);
          if (online) {
            out.routeToOwner = "true";
            out.RoutingCriteria = {
              Steps: [
                {
                  Expiry: { DurationInSeconds: Number(OWNER_EXPIRY[channel] ?? OWNER_EXPIRY.CHAT ?? 60) },
                  Expression: {
                    AttributeCondition: {
                      ComparisonOperator: "Match",
                      MatchCriteria: { AgentsCriteria: { AgentIds: [info.ownerUserId] } },
                    },
                  },
                },
              ],
            };
          }
        }
      } else if (info) {
        console.warn(`case ${caseId} tenant mismatch (${info.merchantId} != ${merchantId}); standard routing`);
      }
    } catch (err) {
      console.error(`case lookup failed for ${caseId}; standard routing`, err);
    }
  }

  console.log("contact-context result", JSON.stringify({ contactId, merchantId, ...out }));
  return out;
};
