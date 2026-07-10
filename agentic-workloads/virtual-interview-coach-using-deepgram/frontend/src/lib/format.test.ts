// Regression guard for the NUMERIC-string score bug: Postgres NUMERIC columns serialize as JSON
// strings, and an uncoerced string blanked the whole report screen. num()/fmt()/pct() must accept
// number | string | null | garbage and never throw.

import { describe, expect, it } from "vitest";
import { fmt, num, pct } from "./format";

describe("num", () => {
  it("passes numbers through", () => {
    expect(num(7.5)).toBe(7.5);
    expect(num(0)).toBe(0);
  });

  it("coerces NUMERIC strings (the past blank-report bug)", () => {
    expect(num("7.5")).toBe(7.5);
    expect(num("0")).toBe(0);
  });

  it("returns null for null/undefined/garbage", () => {
    expect(num(null)).toBeNull();
    expect(num(undefined)).toBeNull();
    expect(num("not a number")).toBeNull();
    expect(num(NaN)).toBeNull();
    expect(num(Infinity)).toBeNull();
  });
});

describe("fmt", () => {
  it("formats to one decimal", () => {
    expect(fmt(7)).toBe("7.0");
    expect(fmt("6.25")).toBe("6.3");
  });

  it("renders an em-dash for missing values", () => {
    expect(fmt(null)).toBe("—");
    expect(fmt("bogus")).toBe("—");
  });
});

describe("pct", () => {
  it("maps a 0-10 score onto 0-100", () => {
    expect(pct(7.5)).toBe(75);
    expect(pct("10")).toBe(100);
  });

  it("clamps out-of-range and missing values", () => {
    expect(pct(12)).toBe(100);
    expect(pct(-1)).toBe(0);
    expect(pct(null)).toBe(0);
  });
});
