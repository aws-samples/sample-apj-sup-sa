import { ConnectClient, DescribeContactCommand, StartWebRTCContactCommand } from "@aws-sdk/client-connect";
import { ConnectCasesClient, GetCaseCommand, ListFieldsCommand } from "@aws-sdk/client-connectcases";

// POST /screenshare/start            — starts an Amazon Connect in-app/web call (WebRTC
// contact) on the merchant's behalf, with the customer allowed to SHARE THEIR
// SCREEN with the agent.
// GET  /screenshare/status/{contactId} — whether an agent is connected yet / the
// call ended (DescribeContact; the merchant UI polls it while waiting). Fronted by API Gateway HTTP API + Cognito JWT
// authorizer, so the token is already validated before we run.
//
// MERCHANT ISOLATION (server-enforced), same model as ChatApiFn:
//   - merchant_id / merchant_name / email on the contact come from the VALIDATED
//     JWT, never from the client body.
//   - A session started FROM a case (caseId in the body) is only allowed after
//     the case is verified to belong to the caller's tenant.
//   - The returned ConnectionData (Chime meeting + attendee join token) and
//     ParticipantToken are scoped to this one contact.
const REGION = process.env.AWS_REGION!;
const INSTANCE_ID = process.env.CONNECT_INSTANCE_ID!;
const CONTACT_FLOW_ID = (process.env.CONTACT_FLOW_ARN || "").split("/").pop() || "";
const CASES_DOMAIN_ID = process.env.CASES_DOMAIN_ID || "";
// Let the merchant also send camera video (off by default: screen + voice only).
const CUSTOMER_VIDEO = process.env.CUSTOMER_VIDEO === "true";

const connect = new ConnectClient({ region: REGION });
const cases = new ConnectCasesClient({ region: REGION });

function json(statusCode: number, body: unknown) {
  return { statusCode, headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
}

// Field id of the tenant key ("merchant_id") on cases, resolved by name once per
// warm container so nothing is hardcoded to generated ids.
let merchantField: string | null = null;
async function merchantFieldId(): Promise<string> {
  if (merchantField) return merchantField;
  let next: string | undefined;
  do {
    const r = await cases.send(new ListFieldsCommand({ domainId: CASES_DOMAIN_ID, maxResults: 100, nextToken: next }));
    const hit = (r.fields ?? []).find((f) => f.name === "merchant_id");
    if (hit?.fieldId) return (merchantField = hit.fieldId);
    next = r.nextToken;
  } while (next);
  throw new Error("Cases field merchant_id not found");
}

async function caseTenant(caseId: string): Promise<string> {
  const fieldId = await merchantFieldId();
  const res = await cases.send(new GetCaseCommand({ domainId: CASES_DOMAIN_ID, caseId, fields: [{ id: fieldId }] }));
  const f = (res.fields ?? []).find((x) => x.id === fieldId);
  return (f?.value?.stringValue as string) ?? "";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const handler = async (event: any) => {
  try {
    const claims = event.requestContext?.authorizer?.jwt?.claims ?? {};
    const rawGroups = claims["cognito:groups"];
    const groups: string[] = Array.isArray(rawGroups)
      ? rawGroups
      : typeof rawGroups === "string"
      ? rawGroups.replace(/[[\]]/g, "").split(/[\s,]+/).filter(Boolean)
      : [];
    // Screen sharing is a customer (merchant) capability.
    if (!groups.includes("merchant")) return json(403, { message: "Not authorized" });
    const merchantId = (claims["custom:merchant_id"] as string) || "";
    const merchantName = (claims["custom:merchant_name"] as string) || "";
    const email = (claims["email"] as string) || "";
    if (!merchantId) return json(403, { message: "No merchant tenant on token" });
    if (!INSTANCE_ID || !CONTACT_FLOW_ID) return json(500, { message: "Screen sharing is not configured" });

    // GET /screenshare/status/{contactId}: only the contact's own tenant may ask.
    const statusId = event.pathParameters?.contactId as string | undefined;
    if (statusId) {
      try {
        const c = (await connect.send(new DescribeContactCommand({ InstanceId: INSTANCE_ID, ContactId: statusId }))).Contact;
        if (!c || c.Attributes?.merchant_id !== merchantId) return json(404, { message: "Not found" });
        const state = c.DisconnectTimestamp ? "ended" : c.AgentInfo?.ConnectedToAgentTimestamp ? "connected" : "waiting";
        return json(200, { state });
      } catch {
        return json(404, { message: "Not found" });
      }
    }

    const body = event.body ? JSON.parse(event.body) : {};
    const caseId = typeof body.caseId === "string" && body.caseId.trim() ? body.caseId.trim() : "";
    if (caseId) {
      if (!CASES_DOMAIN_ID)
        return json(500, { message: "Cases integration is not configured" });
      try {
        if ((await caseTenant(caseId)) !== merchantId) return json(403, { message: "Forbidden" });
      } catch {
        return json(404, { message: "Case not found" });
      }
    }

    const attributes: Record<string, string> = {
      merchant_id: merchantId,
      merchant_name: merchantName,
      email,
      source: caseId ? "merchant-case-screenshare" : "merchant-screenshare",
      ...(caseId ? { case_id: caseId } : {}),
    };
    for (const k of Object.keys(attributes)) if (!attributes[k]) delete attributes[k];

    const res = await connect.send(
      new StartWebRTCContactCommand({
        InstanceId: INSTANCE_ID,
        ContactFlowId: CONTACT_FLOW_ID,
        ParticipantDetails: { DisplayName: (merchantName || email || "Merchant").slice(0, 256) },
        Attributes: attributes,
        Description: caseId ? `Screen share about case ${caseId}` : "Merchant screen share",
        // Customer: voice + screen share (+ optional camera). Agent: voice +
        // optional camera so the merchant can see who is helping.
        AllowedCapabilities: {
          Customer: { ScreenShare: "SEND", ...(CUSTOMER_VIDEO ? { Video: "SEND" } : {}) },
          Agent: { Video: "SEND" },
        },
      })
    );

    return json(200, {
      contactId: res.ContactId,
      participantId: res.ParticipantId,
      participantToken: res.ParticipantToken,
      // Amazon Chime SDK MeetingSessionConfiguration inputs.
      meeting: res.ConnectionData?.Meeting,
      attendee: res.ConnectionData?.Attendee,
    });
  } catch (err) {
    console.error(err);
    return json(500, { message: err instanceof Error ? err.message : "Internal error" });
  }
};
