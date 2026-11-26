import { Badge } from "@/components/ui/Badge";
import type { UserStatus } from "@/lib/types";

const toneByStatus: Record<UserStatus, "neutral" | "blue" | "amber" | "red"> = {
  normal: "neutral",
  warning: "amber",
  blocked: "red",
  exempt: "blue",
};

const labelByStatus: Record<UserStatus, string> = {
  normal: "Normal",
  warning: "Warning",
  blocked: "Blocked",
  exempt: "Exempt",
};

export function StatusBadge({ status }: { status: UserStatus }) {
  return <Badge tone={toneByStatus[status]}>{labelByStatus[status]}</Badge>;
}
