// The beacon must be safe in every failure mode: no token -> no request; fetch rejection ->
// swallowed; correct endpoint + auth header + allowlisted name when it does fire.

import { afterEach, describe, expect, it, vi } from "vitest";
import { reportClientEvent, setClientEventsToken } from "./clientEvents";

afterEach(() => {
  setClientEventsToken(null);
  vi.unstubAllGlobals();
});

describe("reportClientEvent", () => {
  it("does nothing without a token", () => {
    const spy = vi.fn();
    vi.stubGlobal("fetch", spy);
    reportClientEvent("mic_denied");
    expect(spy).not.toHaveBeenCalled();
  });

  it("posts the event name with the bearer token", () => {
    const spy = vi.fn(() => Promise.resolve(new Response("{}")));
    vi.stubGlobal("fetch", spy);
    setClientEventsToken("tok-1");
    reportClientEvent("session_dropped");
    expect(spy).toHaveBeenCalledTimes(1);
    const [url, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/client-events");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-1");
    expect(JSON.parse(String(init.body))).toEqual({ event: "session_dropped" });
  });

  it("swallows fetch failures", () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("network down"))));
    setClientEventsToken("tok-1");
    expect(() => reportClientEvent("render_error")).not.toThrow();
  });
});
