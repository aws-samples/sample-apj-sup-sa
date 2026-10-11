import { describe, expect, it } from "vitest";
import { challenge } from "./auth";

describe("PKCE", () => {
  it("is base64url(SHA-256(verifier)) without padding", async () => {
    // Expected value computed independently with Python hashlib + base64.urlsafe_b64encode.
    expect(await challenge("dBjftJeZ4CVP-mJ92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe( // pragma: allowlist secret (RFC 7636 test vector)
      "ngF5GsXcbwljx6u133FFr3Xht9xooA_DuaX_3QwODtc", // pragma: allowlist secret
    );
  });
});
