import { describe, expect, test } from "vitest";
import { classifyClaims } from "./cognito-auth";

const options = {
  allowed: new Set<string>(),
  adminGroup: "console-admins",
  viewerGroup: "console-viewers",
};

test("admin group grants read and write", () => {
  expect(classifyClaims({
    email: "admin@example.com",
    "cognito:username": "admin",
    "cognito:groups": ["console-admins"],
  }, options)).toMatchObject({ isAdmin: true, isViewer: true });
});

test("viewer group grants read only", () => {
  expect(classifyClaims({
    email: "viewer@example.com",
    "cognito:username": "viewer",
    "cognito:groups": ["console-viewers"],
  }, options)).toMatchObject({ isAdmin: false, isViewer: true });
});

test("an authenticated user without a group fails closed", () => {
  expect(classifyClaims({
    email: "other@example.com",
    "cognito:username": "other",
  }, options)).toMatchObject({ isAdmin: false, isViewer: false });
});

describe("emergency allowlist", () => {
  test("may explicitly grant admin access", () => {
    expect(classifyClaims({
      email: "breakglass@example.com",
      "cognito:username": "breakglass",
    }, {
      ...options,
      allowed: new Set(["breakglass@example.com"]),
    })).toMatchObject({ isAdmin: true, isViewer: true });
  });

  test("empty groups and allowlist do not enable bootstrap access", () => {
    expect(classifyClaims({
      email: "user@example.com",
      "cognito:username": "user",
    }, {
      allowed: new Set(),
      adminGroup: "",
      viewerGroup: "",
    })).toMatchObject({ isAdmin: false, isViewer: false });
  });
});
