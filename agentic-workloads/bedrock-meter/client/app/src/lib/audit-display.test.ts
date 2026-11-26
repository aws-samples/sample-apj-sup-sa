import { describe, expect, test } from "vitest";
import { auditActorDisplay } from "./audit-display";

describe("auditActorDisplay", () => {
  test("collapses the console BFF assumed-role ARN", () => {
    expect(auditActorDisplay(
      "arn:aws:sts::803936485311:assumed-role/BedrockMeterConsoleBffRole/session-id",
    )).toEqual({ label: "BedrockMeter", isBedrockMeter: true });
  });

  test("collapses the direct console BFF role ARN", () => {
    expect(auditActorDisplay(
      "arn:aws:iam::803936485311:role/BedrockMeterConsoleBffRole",
    )).toEqual({ label: "BedrockMeter", isBedrockMeter: true });
  });

  test("presents system events as BedrockMeter", () => {
    expect(auditActorDisplay("system")).toEqual({
      label: "BedrockMeter",
      isBedrockMeter: true,
    });
  });

  test("keeps human and unrelated role actors intact", () => {
    expect(auditActorDisplay("choichul@amazon.com")).toEqual({
      label: "choichul@amazon.com",
      isBedrockMeter: false,
    });
    expect(auditActorDisplay(
      "arn:aws:sts::803936485311:assumed-role/DeploymentRole/session-id",
    )).toEqual({
      label: "arn:aws:sts::803936485311:assumed-role/DeploymentRole/session-id",
      isBedrockMeter: false,
    });
  });
});
