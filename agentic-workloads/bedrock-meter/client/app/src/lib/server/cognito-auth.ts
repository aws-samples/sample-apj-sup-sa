import { CognitoJwtVerifier } from "aws-jwt-verify";

export class UnauthorizedError extends Error {
  constructor(message = "login required") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

// 403, not 401: the caller IS authenticated (valid token) but their identity is not on the
// console allowlist. The handler maps this to a distinct status so the UI can say "not
// authorized" rather than "log in".
export class ForbiddenError extends Error {
  constructor(message = "not authorized") {
    super(message);
    this.name = "ForbiddenError";
  }
}

// Two authorization tiers, by Cognito group membership (cognito:groups claim, managed at
// runtime via admin-add-user-to-group → no redeploy):
//   - ADMIN_GROUP  (console-admins):  full access — every read AND every write.
//   - VIEWER_GROUP (console-viewers): read-only — GET data paths, but NOT writes.
// CONSOLE_ALLOWED_USERS is an optional emergency admin override. Normal access is granted
// only through the runtime-managed Cognito groups.
// requireViewer() passes admins, viewers, or allowlisted users; requireWriter() passes only
// admins or allowlisted users. If NO gate is configured at all (both group names empty AND
const ALLOWED = new Set(
  (process.env.CONSOLE_ALLOWED_USERS || "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean),
);
const ADMIN_GROUP = (process.env.CONSOLE_ADMIN_GROUP || "").trim();
const VIEWER_GROUP = (process.env.CONSOLE_VIEWER_GROUP || "").trim();

// Verifies the Cognito ID token on write requests. Checks RS256 signature against the
// pool JWKS (cached), issuer, audience (=clientId), token_use="id", and expiry — all in
// the library, no hand-rolling. ID token (not access) so it carries aud + the email claim
// used for audit attribution.
let verifier: ReturnType<typeof CognitoJwtVerifier.create> | null = null;
function getVerifier() {
  if (!verifier) {
    const userPoolId = process.env.COGNITO_USER_POOL_ID;
    const clientId = process.env.COGNITO_APP_CLIENT_ID;
    if (!userPoolId || !clientId) {
      throw new Error("Cognito env (COGNITO_USER_POOL_ID / COGNITO_APP_CLIENT_ID) not configured");
    }
    verifier = CognitoJwtVerifier.create({ userPoolId, tokenUse: "id", clientId });
  }
  return verifier;
}

export type Caller = { email: string; username: string; isAdmin: boolean; isViewer: boolean };

export function classifyClaims(
  claims: Record<string, unknown>,
  options: { allowed?: Set<string>; adminGroup?: string; viewerGroup?: string } = {},
): Caller {
  const email = String(claims.email ?? claims["cognito:username"] ?? "unknown");
  const username = String(claims["cognito:username"] ?? "").toLowerCase();
  const groups = Array.isArray(claims["cognito:groups"])
    ? (claims["cognito:groups"] as unknown[]).map(String)
    : [];
  const allowed = options.allowed ?? ALLOWED;
  const adminGroup = options.adminGroup ?? ADMIN_GROUP;
  const viewerGroup = options.viewerGroup ?? VIEWER_GROUP;
  const inList = allowed.has(email.toLowerCase()) || allowed.has(username);
  const isAdmin = inList || (Boolean(adminGroup) && groups.includes(adminGroup));
  const isViewer = isAdmin || (Boolean(viewerGroup) && groups.includes(viewerGroup));
  return { email, username, isAdmin, isViewer };
}

// Verify the Bearer id token (signature/issuer/audience/expiry via aws-jwt-verify) and classify
// the caller's tier from the allowlist + cognito:groups. Throws UnauthorizedError (401) on a
// missing/invalid/expired token. Authorization (403) is decided by the require* wrappers.
async function authenticate(authorizationHeader: string | null | undefined): Promise<Caller> {
  const token = authorizationHeader?.replace(/^Bearer\s+/i, "").trim();
  if (!token) throw new UnauthorizedError("login required");
  let claims: Awaited<ReturnType<ReturnType<typeof CognitoJwtVerifier.create>["verify"]>>;
  try {
    claims = await getVerifier().verify(token);
  } catch {
    throw new UnauthorizedError("invalid or expired session");
  }
  return classifyClaims(claims as unknown as Record<string, unknown>);
}

/**
 * Read tier. Passes admins, viewers, or bootstrap-allowlisted users. 401 if unauthenticated,
 * 403 if authenticated but in neither tier. Used on GET data paths when reads require auth.
 */
export async function requireViewer(authorizationHeader: string | null | undefined): Promise<Caller> {
  const caller = await authenticate(authorizationHeader);
  if (!caller.isViewer) throw new ForbiddenError("not authorized for this console");
  return caller;
}

/**
 * Write tier (admins only). 401 if unauthenticated, 403 if authenticated but not an admin
 * (e.g. a read-only viewer). Used on every mutating path (PUT/DELETE/POST).
 */
export async function requireWriter(authorizationHeader: string | null | undefined): Promise<Caller> {
  const caller = await authenticate(authorizationHeader);
  if (!caller.isAdmin) throw new ForbiddenError("write access requires the admin role");
  return caller;
}
