function base64UrlEncode(bytes: Uint8Array): string {
  let str = "";
  bytes.forEach((b) => (str += String.fromCharCode(b)));
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Cryptographically-random URL-safe string for PKCE verifier / state. */
export function randomString(length = 64): string {
  const arr = new Uint8Array(length);
  crypto.getRandomValues(arr);
  return base64UrlEncode(arr).slice(0, length);
}

/** S256 code challenge for a given PKCE verifier. */
export async function sha256Challenge(verifier: string): Promise<string> {
  const data = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return base64UrlEncode(new Uint8Array(digest));
}
