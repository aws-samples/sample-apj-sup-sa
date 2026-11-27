const BFF_ASSUMED_ROLE_PATTERN =
  /^arn:[^:]+:sts::\d{12}:assumed-role\/BedrockMeterConsoleBffRole(?:\/|$)/;
const BFF_ROLE_PATTERN =
  /^arn:[^:]+:iam::\d{12}:role\/BedrockMeterConsoleBffRole$/;

export type AuditActorDisplay = {
  label: string;
  isBedrockMeter: boolean;
};

export function auditActorDisplay(actor: string): AuditActorDisplay {
  const value = actor.trim() || "system";
  const isBedrockMeter =
    value.toLowerCase() === "system"
    || value === "BedrockMeter"
    || BFF_ASSUMED_ROLE_PATTERN.test(value)
    || BFF_ROLE_PATTERN.test(value);

  return {
    label: isBedrockMeter ? "BedrockMeter" : value,
    isBedrockMeter,
  };
}
