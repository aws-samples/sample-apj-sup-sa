import { beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requireWriter: vi.fn(),
  updateUserLimit: vi.fn(),
}));

vi.mock("@/lib/server/meter-backend", () => ({
  BackendRequestError: class BackendRequestError extends Error {},
}));

vi.mock("@/lib/server/console-service", () => ({
  deleteUserLimit: vi.fn(),
  forceUnblock: vi.fn(),
  getBlockedUsers: vi.fn(),
  getLimitsSummary: vi.fn(),
  getOverview: vi.fn(),
  getModelTrend: vi.fn(),
  getUser: vi.fn(),
  getUserHistory: vi.fn(),
  getUserLimit: vi.fn(),
  listAuditPage: vi.fn(),
  listUsersPage: vi.fn(),
  updateQuotaTimeZone: vi.fn(),
  updateUserLimit: mocks.updateUserLimit,
}));

vi.mock("@/lib/server/cognito-auth", () => ({
  requireViewer: vi.fn(),
  requireWriter: mocks.requireWriter,
  ForbiddenError: class ForbiddenError extends Error {},
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

vi.mock("./static-files", () => ({ serveStatic: vi.fn() }));
vi.mock("./security-headers", () => ({ SECURITY_HEADERS: {} }));

import { handler } from "./handler";

beforeEach(() => {
  mocks.requireWriter.mockResolvedValue({ username: "choichul", email: "choichul@example.com" });
  mocks.updateUserLimit.mockResolvedValue({ updated: true });
});

test("allows an admin to update their own limit", async () => {
  const response = await handler({
    requestContext: { http: { method: "PUT" } },
    rawPath: "/api/console/limit-policies/choichul",
    headers: { authorization: "Bearer token" },
    body: JSON.stringify({
      changes: { dailyTokenLimit: 5_000_000, exempt: true },
      reason: "Updated my development allowance",
    }),
  });

  expect(response.statusCode).toBe(200);
  expect(mocks.updateUserLimit).toHaveBeenCalledWith(
    "choichul",
    { dailyTokenLimit: 5_000_000, exempt: true },
    "Updated my development allowance",
    { username: "choichul", email: "choichul@example.com" },
  );
});
