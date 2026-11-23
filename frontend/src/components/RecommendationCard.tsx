import { useState } from "react";
import clsx from "clsx";
import { ArrowRight, Check, ChevronDown, ChevronUp, X } from "lucide-react";
import type { Decision } from "@/api/types";
import { useApprove, useReject } from "@/api/hooks";
import { ApiError } from "@/api/client";
import { hours, liters, pct, shortId } from "@/lib/format";

/** One recommendation in the brief §9 alert format, with the reasoning one click away. */
export function RecommendationCard({ d, tick, canAct }: { d: Decision; tick: number; canAct: boolean }) {
  const [open, setOpen] = useState(false);
  const approve = useApprove();
  const reject = useReject();
  const err = (approve.error ?? reject.error) as ApiError | null;
  const ticksLeft = d.expires_at_tick - tick;
  const lowConfidence = d.confidence < 0.7;

  return (
    <article className="rounded-lg border border-bg-border bg-bg-panel" data-testid={`rec-${d.id}`}>
      <header className="flex flex-wrap items-center gap-2 border-b border-bg-border px-4 py-2 text-xs">
        <span className="font-semibold text-ink">Recommendation #{d.id}</span>
        <span className="text-ink-faint">t={d.tick} · {d.policy_version}</span>
        <span className={clsx("rounded px-1.5 py-0.5", lowConfidence ? "bg-risk-medium/15 text-risk-medium" : "bg-state-healthy/15 text-state-healthy")}>
          confidence {pct(d.confidence, 0)}{lowConfidence ? " · human review required" : ""}
        </span>
        {d.degraded && <span className="rounded bg-risk-high/15 px-1.5 py-0.5 text-risk-high">degraded data</span>}
        <span className="ml-auto text-ink-muted">expires in {ticksLeft} ticks</span>
      </header>

      <div className="grid gap-3 p-4 md:grid-cols-2">
        {(d.impact?.targets ?? []).map((t) => (
          <div key={`${t.station_id}-${t.fuel_type}`} className="rounded-md bg-bg-raised p-3 font-mono text-[12px] leading-5" data-testid="alert-card">
            <div className="mb-1 font-sans text-[11px] font-semibold uppercase tracking-wide text-risk-high">Alert</div>
            <div>Station: <b className="text-ink">{t.station_name}</b></div>
            <div>Fuel: <b className="text-ink">{t.fuel_type}</b></div>
            <div className="mt-1">Projected stockout: <b className="text-ink">{t.projected_stockout_hours == null ? "beyond 24 h" : t.projected_stockout_hours === 0 ? "NOW (dry)" : hours(t.projected_stockout_hours)}</b></div>
            <div>Current inventory: {liters(t.current_inventory)}</div>
            <div>Expected demand (12 h): {liters(t.expected_demand_12h)}</div>
            <div className="mt-1">Recommended allocation:</div>
            {t.recommended.map((r, i) => (
              <div key={i} className="pl-2 text-ink">{liters(r.liters)} from {shortId(r.from).toUpperCase()}</div>
            ))}
            <div className="mt-1">Expected result:</div>
            <div className="flex items-center gap-1 pl-2 text-ink" data-testid="risk-before-after">
              Stockout risk <b className="text-risk-critical">{pct(t.stockout_prob_before, 0)}</b>
              <ArrowRight size={12} /> <b className="text-state-healthy">{pct(t.stockout_prob_after, 0)}</b>
            </div>
            <div className="pl-2 text-ink-muted">unmet {liters(t.unmet_before)} → {liters(t.unmet_after)}</div>
          </div>
        ))}
      </div>

      <div className="px-4 pb-3">
        <button className="flex items-center gap-1 text-xs text-brand" onClick={() => setOpen(!open)} data-testid="why-toggle">
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />} Why, constraints, alternatives
        </button>
        {open && d.explanation && (
          <div className="mt-2 grid gap-3 text-xs text-ink-muted md:grid-cols-3">
            <div>
              <div className="mb-1 font-semibold text-ink">Signals</div>
              <ul className="list-disc pl-4">{d.explanation.why_at_risk.map((w, i) => <li key={i}>{w.text}</li>)}</ul>
              <div className="mt-1">Forecast: {d.explanation.forecast_model}</div>
            </div>
            <div>
              <div className="mb-1 font-semibold text-ink">Constraints respected</div>
              <ul className="list-disc pl-4">{d.explanation.constraints.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </div>
            <div>
              <div className="mb-1 font-semibold text-ink">Lines</div>
              <ul className="space-y-0.5">
                {d.lines.map((l) => (
                  <li key={l.id} className="font-mono">{liters(l.quantity)} {l.fuel_type} via {shortId(l.route_id)} · {l.status}</li>
                ))}
              </ul>
              {d.explanation.unserved && d.explanation.unserved.length > 0 && (
                <div className="mt-1 text-risk-medium" title={d.explanation.unserved.join("\n")}>
                  Waiting for next tick (dispatch cap / route): {d.explanation.unserved.slice(0, 2).map((u) => u.split(":")[0]).join("; ")}
                  {d.explanation.unserved.length > 2 ? ` +${d.explanation.unserved.length - 2} more` : ""}
                </div>
              )}
              <div className="mt-1">Alternative: do nothing → risk stays at the "before" value above.</div>
            </div>
          </div>
        )}
      </div>

      <footer className="flex flex-wrap items-center gap-2 border-t border-bg-border px-4 py-2">
        <span className="text-sm font-medium text-ink">{d.summary}</span>
        <div className="ml-auto flex gap-2">
          <button disabled={!canAct || reject.isPending} onClick={() => reject.mutate({ id: d.id, reason_code: "NOT_NEEDED" })}
            className="flex items-center gap-1 rounded border border-bg-border px-3 py-1 text-sm text-ink-muted hover:bg-bg-raised disabled:opacity-40"
            data-testid="reject-btn"><X size={14} /> Reject</button>
          <button disabled={!canAct || approve.isPending} onClick={() => approve.mutate({ id: d.id })}
            className="flex items-center gap-1 rounded bg-brand px-3 py-1 text-sm font-medium text-white hover:bg-brand/80 disabled:opacity-40"
            data-testid="approve-btn"><Check size={14} /> {approve.isPending ? "Sending…" : "Approve & dispatch"}</button>
        </div>
        {!canAct && <div className="w-full text-right text-xs text-ink-faint">Enter the operator token (top right) to act.</div>}
        {err && <div className="w-full text-right text-xs text-state-down">{err.code}: {err.message}</div>}
      </footer>
    </article>
  );
}
