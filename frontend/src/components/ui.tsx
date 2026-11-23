import clsx from "clsx";
import type { ReactNode } from "react";
import { AlertOctagon, AlertTriangle, Info } from "lucide-react";
import type { Fuel, RiskLevel, Severity } from "@/api/types";

export function Card({ title, right, children, className, testId }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; testId?: string;
}) {
  return (
    <section className={clsx("rounded-lg border border-bg-border bg-bg-panel", className)} data-testid={testId}>
      {title && (
        <header className="flex items-center justify-between border-b border-bg-border px-4 py-2">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Kpi({ label, value, sub, tone = "neutral", testId }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "warn" | "bad" | "neutral"; testId?: string;
}) {
  const color = { good: "text-state-healthy", warn: "text-state-degraded", bad: "text-state-down", neutral: "text-ink" }[tone];
  return (
    <div className="rounded-lg border border-bg-border bg-bg-panel px-4 py-3" data-testid={testId}>
      <div className="text-xs uppercase tracking-wide text-ink-muted">{label}</div>
      <div className={clsx("mt-1 text-2xl font-semibold tabular-nums", color)}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-ink-faint">{sub}</div>}
    </div>
  );
}

const RISK_CLS: Record<RiskLevel, string> = {
  LOW: "bg-risk-low/15 text-risk-low",
  MEDIUM: "bg-risk-medium/15 text-risk-medium",
  HIGH: "bg-risk-high/15 text-risk-high",
  CRITICAL: "bg-risk-critical/20 text-risk-critical",
};

export function RiskChip({ level }: { level: RiskLevel }) {
  return <span className={clsx("rounded px-1.5 py-0.5 text-[11px] font-semibold", RISK_CLS[level])}>{level}</span>;
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  const map = {
    CRITICAL: { cls: "bg-risk-critical/20 text-risk-critical", Icon: AlertOctagon },
    WARNING: { cls: "bg-risk-medium/15 text-risk-medium", Icon: AlertTriangle },
    INFO: { cls: "bg-brand/15 text-brand", Icon: Info },
  } as const;
  const { cls, Icon } = map[severity];
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold", cls)}>
      <Icon size={12} aria-hidden /> {severity}
    </span>
  );
}

const FUEL_CLS: Record<Fuel, string> = { DIESEL: "bg-fuel-diesel", PETROL: "bg-fuel-petrol", OCTANE: "bg-fuel-octane" };

/** Inventory bar: solid = in tank, hatched = on the way. Colour and number together (never colour alone). */
export function FuelBar({ fuel, inventory, inflight = 0, capacity, risk }: {
  fuel: Fuel; inventory: number; inflight?: number; capacity: number; risk?: RiskLevel;
}) {
  const pct = Math.max(0, Math.min(100, (inventory / capacity) * 100));
  const inPct = Math.max(0, Math.min(100 - pct, (inflight / capacity) * 100));
  return (
    <div className="flex items-center gap-2 text-xs" data-testid={`fuelbar-${fuel}`}>
      <span className="w-12 text-ink-muted">{fuel.slice(0, 3)}</span>
      <div className="relative h-2.5 flex-1 overflow-hidden rounded bg-bg-raised" title={`${Math.round(inventory)} / ${capacity} L`}>
        <div className={clsx("absolute inset-y-0 left-0", FUEL_CLS[fuel], risk === "CRITICAL" && "animate-pulse")} style={{ width: `${pct}%` }} />
        <div className="absolute inset-y-0 bg-ink-faint/50" style={{ left: `${pct}%`, width: `${inPct}%` }} />
      </div>
      <span className="w-10 text-right tabular-nums text-ink-muted">{Math.round(pct)}%</span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded border border-dashed border-bg-border p-6 text-center text-sm text-ink-muted">{children}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="rounded border border-state-down/40 bg-state-down/10 p-3 text-sm text-state-down">Unavailable: {msg}. Retrying…</div>;
}
