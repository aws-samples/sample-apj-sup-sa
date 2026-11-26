import { expect, test } from "vitest";
import { SECURITY_HEADERS } from "./security-headers";

test("console responses deny framing and unsafe content types", () => {
  expect(SECURITY_HEADERS["x-frame-options"]).toBe("DENY");
  expect(SECURITY_HEADERS["x-content-type-options"]).toBe("nosniff");
  expect(SECURITY_HEADERS["strict-transport-security"]).toContain("max-age=");
  expect(SECURITY_HEADERS["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(SECURITY_HEADERS["content-security-policy"]).toContain("object-src 'none'");
});
