import { useWorldState } from "@/api/hooks";
import { FUELS } from "@/api/types";
import { Card, ErrorBox, RiskChip } from "@/components/ui";
import { Contingency } from "@/components/Contingency";
import { hours, liters, pct, RISK_ORDER, shortName } from "@/lib/format";

/** Risk board: every station × fuel, Monte Carlo over the forecast's uncertainty (300 paths, 12 h). */
export default function Risk() {
  const { data: s, error, isLoading } = useWorldState();
  if (isLoading) return <div className="animate-pulse text-ink-muted">Loading…</div>;
  if (error || !s) return <ErrorBox error={error} />;
  const name = new Map(s.stations.map((st) => [st.id, shortName(st.name)]));
  const rows = [...s.risk].sort((a, b) => RISK_ORDER[b.level] - RISK_ORDER[a.level] || b.prob_12h - a.prob_12h);
  return (
    <div className="space-y-4">
      <Contingency />
      <Card title="Stockout risk heatmap (next 12 h)">
        <table className="text-sm" data-testid="risk-heatmap">
          <thead><tr><th />{FUELS.map((f) => <th key={f} className="px-4 text-xs text-ink-faint">{f}</th>)}</tr></thead>
          <tbody>
            {s.stations.map((st) => (
              <tr key={st.id}>
                <td className="pr-4 text-ink-muted">{name.get(st.id)}</td>
                {FUELS.map((f) => {
                  const r = s.risk.find((x) => x.station_id === st.id && x.fuel_type === f);
                  const p = r?.prob_12h ?? 0;
                  return (
                    <td key={f} className="p-1">
                      <div className="w-24 rounded px-2 py-1 text-center font-semibold tabular-nums"
                        style={{ background: `rgba(239,68,68,${0.08 + p * 0.6})` }}>{pct(p, 0)}</div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Detail">
        <table className="w-full text-xs">
          <thead className="text-left text-ink-faint">
            <tr><th>Station</th><th>Fuel</th><th>Level</th><th className="text-right">P(dry 12 h)</th><th className="text-right">P(dry before a truck)</th>
              <th className="text-right">Dry in</th><th className="text-right">Inventory</th><th className="text-right">On the way</th><th className="text-right">Demand 12 h</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.station_id}-${r.fuel_type}`} className="border-t border-bg-border">
                <td className="py-1">{name.get(r.station_id)}</td><td>{r.fuel_type}</td><td><RiskChip level={r.level} /></td>
                <td className="text-right">{pct(r.prob_12h, 0)}</td><td className="text-right">{pct(r.prob_lead, 0)}</td>
                <td className="text-right">{hours(r.hours_to_stockout)}</td><td className="text-right">{liters(r.inventory)}</td>
                <td className="text-right">{liters(r.inflight)}</td><td className="text-right">{liters(r.expected_demand_12h)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-[11px] text-ink-faint">Monte Carlo: 300 correlated demand paths from the forecast's 80% band, our in-flight shipments and outage windows.</p>
      </Card>
    </div>
  );
}
