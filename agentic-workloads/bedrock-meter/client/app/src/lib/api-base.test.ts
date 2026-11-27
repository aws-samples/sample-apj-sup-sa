import { afterEach, expect, test, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

test("production ignores a stale development BFF address", async () => {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("NEXT_PUBLIC_CONSOLE_API_BASE", "https://old.example.invalid/api/console");
  const { API_BASE } = await import("./api");
  expect(API_BASE).toBe("/api/console");
});

test("development can still use a remote BFF", async () => {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("NEXT_PUBLIC_CONSOLE_API_BASE", "https://dev.example.invalid/api/console/");
  const { API_BASE } = await import("./api");
  expect(API_BASE).toBe("https://dev.example.invalid/api/console");
});
