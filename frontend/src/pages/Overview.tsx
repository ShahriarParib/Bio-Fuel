import { Link } from "react-router-dom";
import { useAlerts, useWorldState } from "@/api/hooks";
import type { Fuel, RiskItem } from "@/api/types";
import { FUELS } from "@/api/types";
import { ImpactMeter } from "@/components/ImpactMeter";
import { NetworkGraph } from "@/components/NetworkGraph";
import { Card, Empty, ErrorBox, FuelBar, Kpi, RiskChip, SeverityBadge } from "@/components/ui";
import { ShiftBrief } from "@/components/ShiftBrief";
import { hours, liters, pct, RISK_ORDER, shortName } from "@/lib/format";

export default function Overview() {
  const { data: s, error, isLoading } = useWorldState();
  const alerts = useAlerts();
  if (isLoading) return <div className="animate-pulse text-ink-muted">Loading the network…</div>;
  if (error || !s) return <ErrorBox error={error} />;

  const riskOf = new Map<string, RiskItem>(s.risk.map((r) => [`${r.station_id}:${r.fuel_type}`, r]));
  const coverOf = new Map((s.cover?.stations ?? []).map((c) => [`${c.station_id}:${c.fuel_type}`, c.hours_of_cover]));
  const crit = s.alerts_open_count?.CRITICAL ?? 0;
  const warn = s.alerts_open_count?.WARNING ?? 0;
  const minCover = Math.min(...FUELS.map((f) => s.cover?.network[f]?.cover_days ?? Infinity));
  const sl = s.metrics.service_level;

  return (
    <div className="space-y-4" data-testid="overview">
      <ShiftBrief />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Service level" value={pct(sl, 2)} sub="simulator ground truth" tone={sl >= 0.99 ? "good" : sl >= 0.95 ? "warn" : "bad"} testId="kpi-sl" />
        <Kpi label="Unmet demand" value={liters(s.metrics.unmet_liters)} sub={`served ${liters(s.metrics.served_liters)}`} tone={s.metrics.unmet_liters > 0 ? "bad" : "good"} />
        <Kpi label="Failed shipments" value={s.metrics.allocation_failures} sub="fuel destroyed in transit" tone={s.metrics.allocation_failures ? "bad" : "good"} testId="kpi-failures" />
        <Kpi label="Open alerts" value={`${crit} / ${warn}`} sub="critical / warning" tone={crit ? "bad" : warn ? "warn" : "good"} />
        <Kpi label="Awaiting approval" value={s.pending_recommendations.length} sub={s.operating_mode === "autopilot" ? "autopilot on" : "advisory mode"} tone={s.pending_recommendations.length ? "warn" : "neutral"} />
        <Kpi label="Network cover" value={Number.isFinite(minCover) ? `${minCover.toFixed(1)} d` : "—"} sub={s.cover?.supply_ends_tick != null ? `last supply t=${s.cover.supply_ends_tick}` : "no more supply scheduled"} tone={minCover < 1 ? "bad" : minCover < 2 ? "warn" : "neutral"} />
      </div>

      <ImpactMeter m={s.impact_meter} stations={s.stations} />

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Network (live)" className="xl:col-span-2" right={<span className="text-xs text-ink-faint">bars: tank level · border: worst risk</span>}>
          <NetworkGraph depots={s.depots} stations={s.stations} routes={s.routes} allocations={s.allocations_active} risk={s.risk} />
        </Card>
        <Card title="Alerts" right={<Link to="/alerts" className="text-xs text-brand">all →</Link>}>
          {(alerts.data?.items ?? []).length === 0 ? <Empty>No open alerts.</Empty> : (
            <ul className="space-y-2">
              {alerts.data!.items.slice(0, 6).map((a) => (
                <li key={a.fingerprint} className="rounded bg-bg-raised p-2 text-xs">
                  <div className="flex items-center gap-2"><SeverityBadge severity={a.severity} /><span className="text-ink-faint">t={a.opened_tick}</span></div>
                  <div className="mt-1 font-medium text-ink">{a.title}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Stations" className="xl:col-span-2" right={<span className="text-xs text-ink-faint">grey = on the way</span>}>
          <div className="grid gap-3 md:grid-cols-2">
            {s.stations.map((st) => {
              const worst = FUELS.map((f) => riskOf.get(`${st.id}:${f}`)?.level ?? "LOW")
                .sort((a, b) => RISK_ORDER[b] - RISK_ORDER[a])[0];
              return (
                <div key={st.id} className="rounded-md bg-bg-raised p-3" data-testid={`station-${st.id}`}>
                  <div className="mb-2 flex items-center gap-2">
                    <span className="font-medium">{shortName(st.name)}</span>
                    {st.status === "OUTAGE" ? <span className="rounded bg-state-unknown/30 px-1.5 text-[11px]">OUTAGE</span> : <RiskChip level={worst} />}
                    {st.demand_multiplier !== 1 && <span className="text-[11px] text-risk-high">demand ×{st.demand_multiplier.toFixed(2)}</span>}
                  </div>
                  {FUELS.map((f: Fuel) => {
                    const r = riskOf.get(`${st.id}:${f}`);
                    return (
                      <div key={f} className="mb-1">
                        <FuelBar fuel={f} inventory={st.inventory[f]} inflight={r?.inflight ?? 0} capacity={st.capacity[f]} risk={r?.level} />
                        <div className="ml-14 text-[10px] text-ink-faint">
                          {liters(st.inventory[f])} · cover {hours(coverOf.get(`${st.id}:${f}`) ?? null)}
                          {r && r.level !== "LOW" && <> · <span className="text-risk-high">dry in {hours(r.hours_to_stockout)} ({pct(r.prob_12h, 0)})</span></>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </Card>
        <div className="space-y-4">
          <Card title="Fuel budget (whole network)">
            <table className="w-full text-xs">
              <thead className="text-ink-faint"><tr><th className="text-left">Fuel</th><th className="text-right">Available</th><th className="text-right">Next 24 h</th><th className="text-right">Cover</th></tr></thead>
              <tbody>
                {FUELS.map((f) => {
                  const c = s.cover?.network[f];
                  return (
                    <tr key={f} className="border-t border-bg-border">
                      <td className="py-1">{f}</td><td className="text-right">{liters(c?.available_liters)}</td>
                      <td className="text-right">{liters(c?.demand_next_24h)}</td>
                      <td className={`text-right font-semibold ${c?.cover_days != null && c.cover_days < 1.5 ? "text-risk-high" : ""}`}>{c?.cover_days?.toFixed(1) ?? "—"} d</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="mt-2 text-[11px] text-ink-faint">Depots + stations + on the road + scheduled supply vs forecast demand.</p>
          </Card>
          <Card title="Forecast quality">
            {s.forecast ? (
              <dl className="grid grid-cols-2 gap-y-1 text-xs">
                <dt className="text-ink-muted">Model</dt><dd className="text-right">{s.forecast.model}</dd>
                <dt className="text-ink-muted">Source</dt>
                <dd className={`text-right ${s.forecast.source === "fallback" ? "text-risk-high" : "text-state-healthy"}`}>{s.forecast.source === "fallback" ? "FALLBACK (in-engine)" : "prediction service"}</dd>
                <dt className="text-ink-muted">Error (WAPE)</dt><dd className="text-right">{pct(s.forecast.wape)}</dd>
                <dt className="text-ink-muted">Noise floor</dt><dd className="text-right">{pct(s.forecast.noise_floor_wape)}</dd>
                <dt className="text-ink-muted">80% band coverage</dt><dd className="text-right">{pct(s.forecast.coverage_80, 0)}</dd>
              </dl>
            ) : <Empty>Waiting for the first forecast…</Empty>}
          </Card>
        </div>
      </div>
    </div>
  );
}
