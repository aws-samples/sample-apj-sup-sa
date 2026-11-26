// BFF behind a public HTTP API ($default). Reuses the same server libs as the old Next route
// (console-service / meter-backend / sigv4 / aws-credentials), plus Cognito JWT verification.
// Allowlist-only: EVERY data route (read and write) requires a verified, allowlisted Cognito id
// token (Bearer); only /api/console/config and the static UI are anonymous. Signs Core API
// calls with the BFF's own execution-role creds (Core accepts the role name as an admin caller).
import { BackendRequestError } from "@/lib/server/meter-backend";
import {
  deleteUserLimit,
  forceUnblock,
  getBlockedUsers,
  getLimitsSummary,
  getOverview,
  getModelTrend,
  getUser,
  getUserHistory,
  getUserLimit,
  listAuditPage,
  listUsersPage,
  updateQuotaTimeZone,
  updateUserLimit,
} from "@/lib/server/console-service";
import { requireViewer, requireWriter, ForbiddenError, UnauthorizedError } from "@/lib/server/cognito-auth";
import { serveStatic } from "./static-files";
import { SECURITY_HEADERS } from "./security-headers";
import type { ModelTrendMetric, ModelTrendPeriod, TrendGranularity, UserStatus } from "@/lib/types";

type LambdaEvent = {
  requestContext?: { http?: { method?: string } };
  rawPath?: string;
  rawQueryString?: string;
  headers?: Record<string, string | undefined>;
  body?: string;
  isBase64Encoded?: boolean;
};

type LambdaResult = {
  statusCode: number;
  headers: Record<string, string>;
  body: string;
};

// Whether GET reads require a logged-in viewer/admin. Fail-CLOSED: anything other than the
// literal "false" (unset, empty, typo, "TRUE", "1") means auth IS required. Only the exact
// string "false" opens anonymous reads. Writes are ALWAYS admin-only regardless of this flag.
const REQUIRE_AUTH_FOR_READS =
  (process.env.CONSOLE_REQUIRE_AUTH_FOR_READS ?? "true").toLowerCase() !== "false";

// Reads are open to any origin; writes need the Authorization (Bearer) header, which CORS
// must explicitly allow. credentials are not used (token is a header, not a cookie).
const CORS_HEADERS: Record<string, string> = {
  "access-control-allow-origin": process.env.CONSOLE_ALLOWED_ORIGIN || "*",
  "access-control-allow-methods": "GET,PUT,POST,DELETE,OPTIONS",
  "access-control-allow-headers": "content-type,authorization",
  "access-control-max-age": "300",
};
const RESPONSE_HEADERS = { ...CORS_HEADERS, ...SECURITY_HEADERS };

function json(statusCode: number, payload: unknown): LambdaResult {
  return {
    statusCode,
    headers: { "content-type": "application/json", "cache-control": "no-store", ...RESPONSE_HEADERS },
    body: JSON.stringify(payload),
  };
}

function errorResult(error: unknown): LambdaResult {
  if (error instanceof UnauthorizedError) {
    return json(401, { error: error.message });
  }
  if (error instanceof ForbiddenError) {
    return json(403, { error: error.message });
  }
  if (error instanceof BackendRequestError) {
    return json(error.status, { error: error.message, details: error.details });
  }
  if (error instanceof Error) {
    const isClientError =
      error.message.startsWith("Invalid IAM username") ||
      error.message === "reason is required" ||
      error.message === "quotaTimeZone is required" ||
      error.message === "changes must be an object" ||
      error.message.startsWith("invalid JSON body");
    const isConfigError =
      error.message.includes("BEDROCK_METER_API_ENDPOINT") ||
      error.message.includes("AWS credentials were not found");
    return json(isClientError ? 400 : isConfigError ? 503 : 500, { error: error.message });
  }
  return json(500, { error: "Unexpected console API error" });
}

function parseBody(event: LambdaEvent): Record<string, unknown> {
  const raw = event.body
    ? event.isBase64Encoded
      ? Buffer.from(event.body, "base64").toString("utf8")
      : event.body
    : "";
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error(`invalid JSON body: ${error instanceof Error ? error.message : "parse failed"}`);
  }
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : {};
}

function reasonFromBody(body: Record<string, unknown>): string {
  const reason = typeof body.reason === "string" ? body.reason.trim() : "";
  if (!reason) throw new Error("reason is required");
  return reason;
}

function changesFromBody(body: Record<string, unknown>): Record<string, unknown> {
  const changes = body.changes === undefined ? body : body.changes;
  if (!changes || typeof changes !== "object" || Array.isArray(changes)) {
    throw new Error("changes must be an object");
  }
  const { reason: _reason, ...rest } = changes as Record<string, unknown>;
  return rest;
}

function statusFilter(value: string | null): UserStatus | "all" | undefined {
  if (!value) return undefined;
  if (value === "all") return "all";
  return ["normal", "warning", "blocked", "exempt"].includes(value) ? (value as UserStatus) : undefined;
}

function isLimitResource(resource: string | undefined): boolean {
  return resource === "limits" || resource === "limit-policies";
}

// The static client calls <site-origin>/<resource...>. Strip a leading /api/console too,
// so the same handler works if proxied under that prefix.
function routeSegments(rawPath: string): string[] {
  const clean = rawPath.replace(/^\/+/, "").replace(/^api\/console\/?/, "");
  return clean ? clean.split("/").map((s) => decodeURIComponent(s)) : [];
}

async function handleGet(segments: string[], qs: URLSearchParams): Promise<LambdaResult> {
  const [resource, username, child] = segments;

  if (resource === "overview") return json(200, await getOverview());

  if (resource === "stats" && username === "model-trend") {
    return json(200, await getModelTrend(
      (qs.get("period") as ModelTrendPeriod) || undefined,
      (qs.get("metric") as ModelTrendMetric) || undefined,
      Number(qs.get("top")) || undefined,
      (qs.get("granularity") as TrendGranularity) || undefined,
      qs.get("from") || undefined,
      qs.get("to") || undefined,
    ));
  }

  if (resource === "users" && !username) {
    return json(200, await listUsersPage({
      status: statusFilter(qs.get("status")),
      q: qs.get("q") || undefined,
      team: qs.get("team") || undefined,
      pageSize: Number(qs.get("pageSize")) || undefined,
      cursor: qs.get("cursor") || undefined,
    }));
  }

  if (resource === "users" && username && child === "history") {
    return json(200, await getUserHistory(
      username, qs.get("period") || "last30d", qs.get("granularity") || "day",
    ));
  }

  if (resource === "users" && username && !child) return json(200, await getUser(username));

  if (isLimitResource(resource) && !username) return json(200, await getLimitsSummary());
  if (isLimitResource(resource) && username && !child) return json(200, await getUserLimit(username));
  if (resource === "settings" && username === "quota" && !child) {
    const summary = await getLimitsSummary();
    return json(200, summary.globalPolicy);
  }

  if (resource === "blocked" && !username) return json(200, await getBlockedUsers());

  if (resource === "audit" && !username) {
    return json(200, await listAuditPage({
      event: qs.get("event") || undefined,
      target: qs.get("target") || undefined,
      from: qs.get("from") || undefined,
      to: qs.get("to") || undefined,
      limit: Number(qs.get("limit")) || undefined,
      cursor: qs.get("cursor") || undefined,
    }));
  }

  return json(404, { error: "not found" });
}

function isApiPath(rawPath: string): boolean {
  const p = rawPath.replace(/^\/+/, "");
  return p === "api" || p.startsWith("api/") || p === "console" || p.startsWith("console/");
}

// Public runtime config the static UI fetches on first load, so the build artifact is
// environment-independent (no Console URL / Cognito client id baked in). clientId, region,
// and userPoolId are NOT secrets. The Core API endpoint is deliberately omitted. Served
// before auth so the UI can boot and render a config-error state regardless of identity.
function consoleConfig(): LambdaResult {
  return json(200, {
    apiBase: "/api/console",
    auth: {
      provider: "cognito",
      region: process.env.BEDROCK_METER_AWS_REGION || "ap-northeast-2",
      userPoolId: process.env.COGNITO_USER_POOL_ID || "",
      appClientId: process.env.COGNITO_APP_CLIENT_ID || "",
      // Whether reads require login (mirrors the BFF's fail-closed parse) — the UI shows the
      // global sign-in gate only when this is true. Writes always require the admin group.
      requireAuthForReads: REQUIRE_AUTH_FOR_READS,
      // The admin group name so the UI can derive write access from the id token's
      // cognito:groups claim and disable write controls for read-only viewers.
      adminGroup: process.env.CONSOLE_ADMIN_GROUP || "",
    },
  });
}

export async function handler(event: LambdaEvent): Promise<LambdaResult> {
  const method = (event.requestContext?.http?.method || "GET").toUpperCase();
  const rawPath = event.rawPath || "/";

  // Single origin: this Lambda serves BOTH the static console UI and the /api/console BFF.
  // Non-API GETs → static files from the bundled export (no public S3 / CloudFront needed).
  if (!isApiPath(rawPath)) {
    if (method !== "GET" && method !== "HEAD") {
      return { statusCode: 405, headers: RESPONSE_HEADERS, body: "" };
    }
    return serveStatic(rawPath);
  }

  if (method === "OPTIONS") {
    return { statusCode: 204, headers: RESPONSE_HEADERS, body: "" };
  }

  const segments = routeSegments(rawPath);
  const qs = new URLSearchParams(event.rawQueryString || "");
  const authHeader = event.headers?.authorization ?? event.headers?.Authorization;

  // Runtime config: no Core API call, no SigV4 signing — serve before auth so the UI can boot
  // (and show a config-error state) regardless of identity.
  if (method === "GET" && segments[0] === "config" && !segments[1]) {
    return consoleConfig();
  }

  try {
    // Caller identity/tier for the signed-in user. Always requires a valid token (even in
    // anonymous-read mode) since it's about WHO you are. The browser uses isAdmin to decide
    // whether to enable write controls — so the UI mirrors the server's exact tier decision
    // (incl. the email/username allowlist), without the allowlist ever being sent to the client.
    if (method === "GET" && segments[0] === "me" && !segments[1]) {
      const caller = await requireViewer(authHeader);
      return json(200, { email: caller.email, isAdmin: caller.isAdmin, isViewer: caller.isViewer });
    }

    if (method === "GET") {
      // Reads need the VIEWER tier (viewers or admins). When REQUIRE_AUTH_FOR_READS is off,
      // reads are anonymous — but a presented token is still verified (and 403'd if not
      // authorized) rather than silently ignored. So we enforce whenever auth is required OR
      // a token was supplied; only a tokenless GET in anonymous-read mode skips the check.
      if (REQUIRE_AUTH_FOR_READS || authHeader) {
        await requireViewer(authHeader);
      }
      return await handleGet(segments, qs);
    }

    // Every mutating path is ADMIN-only and ALWAYS authenticated, regardless of the read
    // toggle. requireWriter 401s anonymous callers and 403s read-only viewers.
    const actor = await requireWriter(authHeader);

    if (method === "PUT") {
      const [resource, username, child] = segments;
      if (resource === "settings" && username === "quota" && !child) {
        const body = parseBody(event);
        const quotaTimeZone = typeof body.quotaTimeZone === "string" ? body.quotaTimeZone : "";
        if (!quotaTimeZone) throw new Error("quotaTimeZone is required");
        return json(200, await updateQuotaTimeZone(
          quotaTimeZone,
          reasonFromBody(body),
          { email: actor.email, username: actor.username },
        ));
      }
      if (!isLimitResource(resource) || !username || child) return json(404, { error: "not found" });
      const body = parseBody(event);
      return json(200, await updateUserLimit(
        username, changesFromBody(body), reasonFromBody(body),
        { email: actor.email, username: actor.username },
      ));
    }

    if (method === "DELETE") {
      const [resource, username, child] = segments;
      if (!isLimitResource(resource) || !username || child) return json(404, { error: "not found" });
      return json(200, await deleteUserLimit(
        username, reasonFromBody(parseBody(event)),
        { email: actor.email, username: actor.username },
      ));
    }

    if (method === "POST") {
      const [resource, action, username, child] = segments;
      if (resource === "admin" && action === "unblock" && username && !child) {
        return json(200, await forceUnblock(
          username, reasonFromBody(parseBody(event)),
          { email: actor.email, username: actor.username },
        ));
      }
      return json(404, { error: "not found" });
    }

    return json(405, { error: "method not allowed" });
  } catch (error) {
    return errorResult(error);
  }
}
