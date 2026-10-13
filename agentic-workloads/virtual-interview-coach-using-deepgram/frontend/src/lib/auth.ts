// Minimal Cognito auth (T012 client side) — no SDK, just the Cognito IDP REST API.
//
// The backend validates the bearer token's `aud` against the app client id, which only the
// Cognito ID token carries (access tokens have no `aud`), so signIn returns the ID TOKEN.
//
// When no Cognito pool is configured (local dev / config.js left blank), isCognitoConfigured()
// is false and the UI uses a dev token the local backend accepts (it reads the unverified
// `sub` when COGNITO_USER_POOL_ID is unset — see backend/src/auth_cognito.py).

interface AppConfig {
  cognitoRegion: string;
  cognitoUserPoolId: string;
  cognitoClientId: string;
}

function config(): AppConfig {
  const c = (window as any).__APP_CONFIG__ ?? {};
  return {
    cognitoRegion: c.cognitoRegion ?? "us-west-2",
    cognitoUserPoolId: c.cognitoUserPoolId ?? "",
    cognitoClientId: c.cognitoClientId ?? "",
  };
}

export function isCognitoConfigured(): boolean {
  const c = config();
  return Boolean(c.cognitoUserPoolId && c.cognitoClientId);
}

// A static dev token (unsigned-ish JWT with a `sub`) for the local no-Cognito path. The local
// backend accepts it; a real deploy never uses this branch because isCognitoConfigured() is true.
export function devToken(): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const payload = btoa(JSON.stringify({ sub: "dev-user", iat: Math.floor(Date.now() / 1000) }));
  return `${header}.${payload}.`;
}

export interface SignInResult {
  idToken: string;
  // Cognito may require a new password on first sign-in for an admin-created user.
  challenge?: "NEW_PASSWORD_REQUIRED";
  session?: string;
}

// USER_PASSWORD_AUTH against the Cognito IDP endpoint. Returns the ID token, or a
// NEW_PASSWORD_REQUIRED challenge that completeNewPassword() finishes.
export async function signIn(email: string, password: string): Promise<SignInResult> {
  const c = config();
  const resp = await cognito(c, "InitiateAuth", {
    AuthFlow: "USER_PASSWORD_AUTH",
    ClientId: c.cognitoClientId,
    AuthParameters: { USERNAME: email, PASSWORD: password },
  });
  if (resp.ChallengeName === "NEW_PASSWORD_REQUIRED") {
    return { idToken: "", challenge: "NEW_PASSWORD_REQUIRED", session: resp.Session };
  }
  return { idToken: resp.AuthenticationResult.IdToken };
}

export async function completeNewPassword(
  email: string,
  newPassword: string,
  session: string
): Promise<SignInResult> {
  const c = config();
  const resp = await cognito(c, "RespondToAuthChallenge", {
    ChallengeName: "NEW_PASSWORD_REQUIRED",
    ClientId: c.cognitoClientId,
    Session: session,
    ChallengeResponses: { USERNAME: email, NEW_PASSWORD: newPassword },
  });
  return { idToken: resp.AuthenticationResult.IdToken };
}

async function cognito(c: AppConfig, action: string, body: unknown): Promise<any> {
  const resp = await fetch(`https://cognito-idp.${c.cognitoRegion}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": `AWSCognitoIdentityProviderService.${action}`,
    },
    body: JSON.stringify(body),
  });
  const json = await resp.json();
  if (!resp.ok) {
    throw new Error(json.message || json.__type || "sign-in failed");
  }
  return json;
}
