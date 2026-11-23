import type { ImpactMeter as Meter, Station } from "@/api/types";
import { liters, pct, shortName, simClock } from "@/lib/format";

/** The exact do-nothing counterfactual: same demand, no deliveries. Not an estimate (see counterfactual.py). */
export function ImpactMeter({ m, stations }: { m: Meter | null | undefined; stations: Station[] }) {
  if (!m?.available || !m.without_platform || !m.with_platform) return null;
  const name = new Map(stations.map((s) => [s.id, shortName(s.name)]));
  const first = m.without_platform.first_dry[0];
  return (
    <section className="grid gap-4 rounded-lg border border-state-healthy/30 bg-gradient-to-r from-state-down/10 via-bg-panel to-state-healthy/10 p-4 md:grid-cols-3"
      data-testid="impact-meter">
      <div>
        <div className="text-xs uppercase tracking-wide text-ink-muted">Without this platform</div>
        <div className="mt-1 text-2xl font-semibold text-state-down">{liters(m.without_platform.unmet_liters)} unmet</div>
        <div className="text-xs text-ink-muted">
          service level {pct(m.without_platform.service_level)} · {m.without_platform.stations_that_ran_dry} stations dry
          {first ? ` · first: ${name.get(first.station_id) ?? first.station_id} ${first.fuel_type} at ${simClock(first.tick)}` : ""}
        </div>
      </div>
      <div>
        <div className="text-xs uppercase tracking-wide text-ink-muted">With it</div>
        <div className="mt-1 text-2xl font-semibold text-state-healthy">{liters(m.with_platform.unmet_liters)} unmet</div>
        <div className="text-xs text-ink-muted">service level {pct(m.with_platform.service_level)}</div>
      </div>
      <div>
        <div className="text-xs uppercase tracking-wide text-ink-muted">Demand protected</div>
        <div className="mt-1 text-2xl font-semibold text-ink">{liters(m.unmet_prevented_liters)}</div>
        <div className="text-[11px] text-ink-faint">Exact counterfactual: the same observed demand replayed with no deliveries since t={m.since_tick}.</div>
      </div>
    </section>
  );
}
