import {
  ConnectCasesClient,
  CreateCaseCommand,
  GetCaseCommand,
  SearchCasesCommand,
  UpdateCaseCommand,
  CreateRelatedItemCommand,
  SearchRelatedItemsCommand,
  type SearchCasesResponseItem,
} from "@aws-sdk/client-connectcases";

// Fronted by API Gateway HTTP API with a Cognito JWT authorizer, so the token
// is already validated (signature/iss/aud/exp) before we run. We read identity
// from the authorizer claims and enforce access here:
//   - admin group   -> full access to all cases (all merchants)
//   - merchant group -> restricted to their own tenant (custom:merchant_id):
//       * list  : only their merchant's cases
//       * create: merchant_id is FORCED from the JWT (client value ignored)
//       * get/patch/comment: allowed only if the case belongs to their tenant
const REGION = process.env.AWS_REGION!;
const DOMAIN_ID = process.env.CASES_DOMAIN_ID!;
const TEMPLATE_ID = process.env.CASES_TEMPLATE_ID!;
const FIELD_SUMMARY = process.env.FIELD_SUMMARY!;
const FIELD_PRIORITY = process.env.FIELD_PRIORITY!;
const FIELD_STATUS = process.env.FIELD_STATUS!;
const FIELD_MERCHANT = process.env.FIELD_MERCHANT!;
const FIELD_MERCHANT_ID = process.env.FIELD_MERCHANT_ID!;

const TITLE = "title";
const CREATED = "created_datetime";
const RETURN_FIELDS = [
  TITLE,
  FIELD_SUMMARY,
  FIELD_PRIORITY,
  FIELD_STATUS,
  FIELD_MERCHANT,
  FIELD_MERCHANT_ID,
  CREATED,
];
// Upper bound on cases returned by GET /cases (pages of 100 until reached).
const MAX_LIST = 1000;

const cases = new ConnectCasesClient({ region: REGION });

function json(statusCode: number, body: unknown) {
  return { statusCode, headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
}
function str(v: string | undefined) {
  return { stringValue: v ?? "" };
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function flatten(fields: any[] | undefined) {
  const byId: Record<string, unknown> = {};
  for (const f of fields ?? []) {
    const v = f.value ?? {};
    byId[f.id] = v.stringValue ?? v.doubleValue ?? v.booleanValue ?? "";
  }
  return {
    title: byId[TITLE] ?? "",
    summary: byId[FIELD_SUMMARY] ?? "",
    priority: byId[FIELD_PRIORITY] ?? "",
    status: byId[FIELD_STATUS] ?? "",
    merchant: byId[FIELD_MERCHANT] ?? "",
    merchantId: byId[FIELD_MERCHANT_ID] ?? "",
    createdAt: byId[CREATED] ?? "",
  };
}

// Reads only the tenant key of a case (for ownership checks).
async function caseTenant(caseId: string): Promise<string> {
  const res = await cases.send(
    new GetCaseCommand({ domainId: DOMAIN_ID, caseId, fields: [{ id: FIELD_MERCHANT_ID }] })
  );
  const f = (res.fields ?? []).find((x) => x.id === FIELD_MERCHANT_ID);
  return (f?.value?.stringValue as string) ?? "";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const handler = async (event: any) => {
  try {
    // The JWT authorizer already verified the token; derive identity + scope.
    const claims = event.requestContext?.authorizer?.jwt?.claims ?? {};
    const rawGroups = claims["cognito:groups"];
    const groups: string[] = Array.isArray(rawGroups)
      ? rawGroups
      : typeof rawGroups === "string"
      ? rawGroups.replace(/[[\]]/g, "").split(/[\s,]+/).filter(Boolean)
      : [];
    const isAdmin = groups.includes("admin");
    const isMerchant = groups.includes("merchant");
    const callerMerchantId = (claims["custom:merchant_id"] as string) || "";
    const callerMerchantName = (claims["custom:merchant_name"] as string) || "";

    if (!isAdmin && !isMerchant) return json(403, { message: "Not authorized" });
    // A merchant token without a tenant tag can't be scoped safely.
    if (isMerchant && !isAdmin && !callerMerchantId)
      return json(403, { message: "No merchant tenant on token" });

    const method: string = event.requestContext?.http?.method ?? "GET";
    const path: string = event.rawPath ?? "/";
    const body = event.body ? JSON.parse(event.body) : {};

    const commentMatch = path.match(/\/cases\/([^/]+)\/comments$/);
    const caseMatch = path.match(/\/cases\/([^/]+)$/);
    const caseId = commentMatch
      ? decodeURIComponent(commentMatch[1])
      : caseMatch
      ? decodeURIComponent(caseMatch[1])
      : null;

    // For merchants, verify the target case belongs to their tenant first.
    async function assertTenantAccess(id: string): Promise<{ ok: true } | { ok: false; res: ReturnType<typeof json> }> {
      if (isAdmin) return { ok: true };
      const tenant = await caseTenant(id);
      if (tenant !== callerMerchantId) return { ok: false, res: json(403, { message: "Forbidden" }) };
      return { ok: true };
    }

    // GET /cases  -> list
    if (method === "GET" && path.endsWith("/cases")) {
      // Merchants: filter by tenant IN the search (not after it), so a page of
      // other tenants' cases can't crowd theirs out. Page through all results.
      const tenantFilter = isAdmin
        ? undefined
        : { field: { equalTo: { id: FIELD_MERCHANT_ID, value: str(callerMerchantId) } } };
      const found: SearchCasesResponseItem[] = [];
      let nextToken: string | undefined;
      do {
        const res = await cases.send(
          new SearchCasesCommand({
            domainId: DOMAIN_ID,
            maxResults: 100,
            nextToken,
            ...(tenantFilter ? { filter: tenantFilter } : {}),
            fields: RETURN_FIELDS.map((id) => ({ id })),
            sorts: [{ fieldId: CREATED, sortOrder: "Desc" }],
          })
        );
        found.push(...(res.cases ?? []));
        nextToken = res.nextToken;
      } while (nextToken && found.length < MAX_LIST);
      let list = found.slice(0, MAX_LIST).map((c) => ({ caseId: c.caseId, ...flatten(c.fields) }));
      // Tenant isolation (defense-in-depth): re-check the tenant on every row.
      if (!isAdmin) list = list.filter((c) => c.merchantId === callerMerchantId);
      return json(200, { cases: list });
    }

    // POST /cases  -> create
    if (method === "POST" && path.endsWith("/cases")) {
      if (!body.title) return json(400, { message: "title is required" });
      // Merchants: force tenant fields from the JWT. Admins: take from body.
      const merchantId = isAdmin ? body.merchantId || "" : callerMerchantId;
      const merchantName = isAdmin ? body.merchant || "" : callerMerchantName;
      const res = await cases.send(
        new CreateCaseCommand({
          domainId: DOMAIN_ID,
          templateId: TEMPLATE_ID,
          fields: [
            { id: TITLE, value: str(body.title) },
            { id: FIELD_SUMMARY, value: str(body.summary) },
            { id: FIELD_PRIORITY, value: str(body.priority || "Medium") },
            { id: FIELD_STATUS, value: str(body.status || "Open") },
            { id: FIELD_MERCHANT, value: str(merchantName) },
            { id: FIELD_MERCHANT_ID, value: str(merchantId) },
          ],
        })
      );
      return json(201, { caseId: res.caseId });
    }

    // POST /cases/{id}/comments -> add comment
    if (method === "POST" && commentMatch && caseId) {
      if (!body.body) return json(400, { message: "comment body is required" });
      const access = await assertTenantAccess(caseId);
      if (!access.ok) return access.res;
      await cases.send(
        new CreateRelatedItemCommand({
          domainId: DOMAIN_ID,
          caseId,
          type: "Comment",
          content: { comment: { body: String(body.body).slice(0, 3000), contentType: "Text/Plain" } },
        })
      );
      return json(201, { added: true });
    }

    // GET /cases/{id} -> detail + comments
    if (method === "GET" && caseMatch && caseId) {
      const detail = await cases.send(
        new GetCaseCommand({ domainId: DOMAIN_ID, caseId, fields: RETURN_FIELDS.map((id) => ({ id })) })
      );
      const flat = flatten(detail.fields);
      // Tenant isolation on read.
      if (!isAdmin && flat.merchantId !== callerMerchantId) return json(403, { message: "Forbidden" });
      // Comment retrieval is best-effort; don't fail the whole detail view.
      let comments: { body: string; createdAt: string }[] = [];
      try {
        const related = await cases.send(
          new SearchRelatedItemsCommand({ domainId: DOMAIN_ID, caseId })
        );
        comments = (related.relatedItems ?? [])
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          .map((r: any) => ({
            body: r.content?.comment?.body ?? "",
            createdAt: r.associationTime ?? r.performedTime ?? "",
          }))
          .filter((c) => c.body);
      } catch (e) {
        console.error("SearchRelatedItems failed", e);
      }
      return json(200, { caseId, ...flat, comments });
    }

    // PATCH /cases/{id} -> update status/priority/summary
    if ((method === "PATCH" || method === "PUT") && caseMatch && caseId) {
      const access = await assertTenantAccess(caseId);
      if (!access.ok) return access.res;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const fields: any[] = [];
      if (body.status !== undefined) fields.push({ id: FIELD_STATUS, value: str(body.status) });
      if (body.priority !== undefined) fields.push({ id: FIELD_PRIORITY, value: str(body.priority) });
      if (body.summary !== undefined) fields.push({ id: FIELD_SUMMARY, value: str(body.summary) });
      if (fields.length === 0) return json(400, { message: "No updatable fields provided" });
      await cases.send(new UpdateCaseCommand({ domainId: DOMAIN_ID, caseId, fields }));
      return json(200, { caseId, updated: true });
    }

    return json(404, { message: "Not found" });
  } catch (err) {
    console.error(err);
    return json(500, { message: err instanceof Error ? err.message : "Internal error" });
  }
};
