import clsx from "clsx";
import { CheckCircle2, AlertTriangle, XCircle, HelpCircle } from "lucide-react";
import type { HealthStatus } from "@/api/types";

/** Colour + icon + text (never colour alone) — 22_UI_SPEC.md §1.5 */
export function StatusPill({ status }: { status: HealthStatus | "Unknown" }) {
  const map = {
    Healthy: { cls: "bg-state-healthy/15 text-state-healthy", Icon: CheckCircle2 },
    Degraded: { cls: "bg-state-degraded/15 text-state-degraded", Icon: AlertTriangle },
    Down: { cls: "bg-state-down/15 text-state-down", Icon: XCircle },
    Unknown: { cls: "bg-state-unknown/15 text-state-unknown", Icon: HelpCircle },
  } as const;
  const { cls, Icon } = map[status] ?? map.Unknown;
  return (
    <span data-testid="status-pill" className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", cls)}>
      <Icon size={14} aria-hidden /> {status}
    </span>
  );
}
