import { ConnectClient, StartChatContactCommand } from "@aws-sdk/client-connect";
import { ConnectCasesClient, GetCaseCommand } from "@aws-sdk/client-connectcases";

// Fronted by API Gateway HTTP API with a Cognito JWT authorizer, so the token
// is already validated (signature/iss/aud/exp) before we run. We derive the
// caller's identity from the authorizer claims and use it to START a chat on
// their behalf.
//
// MERCHANT ISOLATION (server-enforced):
//   - The merchant_id / merchant_name / email attached to the chat contact are
//     taken from the VALIDATED JWT — never from the client body. A merchant
//     therefore cannot open a chat as another tenant.
//   - StartChatContact returns a ParticipantToken scoped to THIS contact only,
//     so a merchant can only ever access their own chat session.
//   - When a chat is started FROM a case (caseId in the body), the case is first
//     verified to belong to the caller's tenant, so a merchant cannot bind a
//     chat to another tenant's case.
const REGION = process.env.AWS_REGION!;
const INSTANCE_ID = process.env.CONNECT_INSTANCE_ID!;
// CfnContactFlow only exposes the ARN; the ContactFlowId is the last ARN segment.
const CONTACT_FLOW_ID = (process.env.CONTACT_FLOW_ARN || "").split("/").pop() || "";
// Separate flow for case-initiated chats (falls back to the standalone flow).
const CASE_CONTACT_FLOW_ID =
  (process.env.CASE_CONTACT_FLOW_ARN || "").split("/").pop() || CONTACT_FLOW_ID;
const CASES_DOMAIN_ID = process.env.CASES_DOMAIN_ID || "";
const FIELD_MERCHANT_ID = process.env.FIELD_MERCHANT_ID || "";

const connect = new ConnectClient({ region: REGION });
const cases = new ConnectCasesClient({ region: REGION });

function json(statusCode: number, body: unknown) {
  return { statusCode, headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
}

// Reads only the tenant key of a case (for the ownership check).
async function caseTenant(caseId: string): Promise<string> {
  const res = await cases.send(
    new GetCaseCommand({ domainId: CASES_DOMAIN_ID, caseId, fields: [{ id: FIELD_MERCHANT_ID }] })
  );
  const f = (res.fields ?? []).find((x) => x.id === FIELD_MERCHANT_ID);
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
    const isAdmin = groups.includes("admin");
    const isMerchant = groups.includes("merchant");

    const merchantId = (claims["custom:merchant_id"] as string) || "";
    const merchantName = (claims["custom:merchant_name"] as string) || "";
    const email = (claims["email"] as string) || "";
    const sub = (claims["sub"] as string) || "";

    if (!isAdmin && !isMerchant) return json(403, { message: "Not authorized" });
    // A merchant token without a tenant tag can't be isolated safely.
    if (isMerchant && !isAdmin && !merchantId)
      return json(403, { message: "No merchant tenant on token" });

    if (!INSTANCE_ID || !CONTACT_FLOW_ID)
      return json(500, { message: "Chat is not configured" });

    const body = event.body ? JSON.parse(event.body) : {};
    const caseId =
      typeof body.caseId === "string" && body.caseId.trim() ? body.caseId.trim() : "";

    // Case-initiated chat: verify the case belongs to the caller's tenant before
    // binding a chat to it (admins skip the tenant check).
    let contactFlowId = CONTACT_FLOW_ID;
    if (caseId) {
      if (!CASES_DOMAIN_ID || !FIELD_MERCHANT_ID)
        return json(500, { message: "Cases integration is not configured" });
      try {
        const tenant = await caseTenant(caseId);
        if (!isAdmin && tenant !== merchantId)
          return json(403, { message: "Forbidden" });
      } catch {
        return json(404, { message: "Case not found" });
      }
      contactFlowId = CASE_CONTACT_FLOW_ID;
    }

    // The display name the agent sees. Identity used for routing/isolation is
    // stamped as attributes below (from the JWT), NOT from this value.
    const displayName =
      (merchantName || email || "Merchant").toString().slice(0, 256);

    // Contact attributes — sourced ONLY from the validated JWT (plus the
    // server-verified case_id). These are readable in the flow and the agent
    // workspace so the agent knows which tenant + case they're handling.
    const attributes: Record<string, string> = {
      merchant_id: merchantId,
      merchant_name: merchantName,
      email,
      user_sub: sub,
      source: caseId ? "merchant-case" : "merchant-dashboard",
      ...(caseId ? { case_id: caseId } : {}),
    };
    // Attribute keys may contain only alphanumeric/dash/underscore; drop empties.
    for (const k of Object.keys(attributes)) {
      if (!attributes[k]) delete attributes[k];
    }

    const initialMessage =
      typeof body.message === "string" && body.message.trim()
        ? { Content: body.message.trim().slice(0, 1024), ContentType: "text/plain" }
        : undefined;

    const res = await connect.send(
      new StartChatContactCommand({
        InstanceId: INSTANCE_ID,
        ContactFlowId: contactFlowId,
        ParticipantDetails: { DisplayName: displayName },
        Attributes: attributes,
        SupportedMessagingContentTypes: ["text/plain", "text/markdown"],
        // Pre-chat authentication: the caller already proved identity via the
        // Cognito JWT, so flag the contact AUTHENTICATED and pass the tenant as
        // the CustomerId (used by flows / Customer Profiles lookups).
        SegmentAttributes: {
          "connect:CustomerAuthentication": {
            ValueMap: { Status: { ValueString: "AUTHENTICATED" } },
          },
        },
        ...(merchantId ? { CustomerId: merchantId } : {}),
        ...(initialMessage ? { InitialMessage: initialMessage } : {}),
      })
    );

    return json(200, {
      contactId: res.ContactId,
      participantId: res.ParticipantId,
      participantToken: res.ParticipantToken,
      region: REGION,
    });
  } catch (err) {
    console.error(err);
    return json(500, { message: err instanceof Error ? err.message : "Internal error" });
  }
};
