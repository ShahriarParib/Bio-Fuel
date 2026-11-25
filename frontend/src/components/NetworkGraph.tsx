import type { Allocation, Depot, Fuel, RiskItem, RiskLevel, Route, Station } from "@/api/types";
import { FUELS } from "@/api/types";
import { RISK_ORDER, shortName } from "@/lib/format";

const W = 760;
const H = 330;
const RISK_STROKE: Record<RiskLevel, string> = { LOW: "#7FA98C", MEDIUM: "#C9B069", HIGH: "#C78D63", CRITICAL: "#D4717A" };
const FUEL_FILL: Record<Fuel, string> = { DIESEL: "#E6E6EA", PETROL: "#9C9CA6", OCTANE: "#5E5E6A" };

function spread<T>(items: T[], top: number, bottom: number): [T, number][] {
  if (items.length === 1) return [[items[0], (top + bottom) / 2]];
  return items.map((it, i) => [it, top + (i * (bottom - top)) / (items.length - 1)]);
}

/** Depots → routes → stations, live. Node data is drawn as text + bars so it reads without colour. */
export function NetworkGraph({ depots, stations, routes, allocations, risk }: {
  depots: Depot[]; stations: Station[]; routes: Route[]; allocations: Allocation[]; risk: RiskItem[];
}) {
  const byRegion = (a: { region_id: string; id: string }, b: { region_id: string; id: string }) =>
    a.region_id.localeCompare(b.region_id) * -1 || a.id.localeCompare(b.id);
  const dPos = new Map(spread([...depots].sort(byRegion), 80, H - 80).map(([d, y]) => [d.id, y]));
  const sPos = new Map(spread([...stations].sort(byRegion), 45, H - 45).map(([s, y]) => [s.id, y]));
  const worst = new Map<string, RiskLevel>();
  for (const r of risk) {
    const cur = worst.get(r.station_id) ?? "LOW";
    if (RISK_ORDER[r.level] > RISK_ORDER[cur]) worst.set(r.station_id, r.level);
  }
  const onRoute = new Map<string, { n: number; liters: number }>();
  for (const a of allocations) {
    const v = onRoute.get(a.route_id) ?? { n: 0, liters: 0 };
    onRoute.set(a.route_id, { n: v.n + 1, liters: v.liters + a.quantity });
  }
  const DX = 150;
  const SX = W - 200;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Fuel network" data-testid="network-graph">
      {routes.map((r) => {
        const y1 = dPos.get(r.source_depot_id);
        const y2 = sPos.get(r.destination_station_id);
        if (y1 == null || y2 == null) return null;
        const down = r.status === "DISRUPTED";
        const flow = onRoute.get(r.id);
        const mx = (DX + 70 + SX) / 2;
        const my = (y1 + y2) / 2;
        return (
          <g key={r.id} data-testid={`edge-${r.id}`}>
            <line x1={DX + 70} y1={y1} x2={SX} y2={y2} stroke={down ? "#D4717A" : flow ? "#8A8A95" : "#24242B"}
              strokeWidth={flow ? 3 : 2} strokeDasharray={down ? "6 5" : undefined} />
            <text x={mx} y={my - 4} textAnchor="middle" className="fill-ink-faint text-[10px]">
              {down ? "DISRUPTED" : `${r.transit_ticks} ticks`}
            </text>
            {flow && (
              <text x={mx} y={my + 10} textAnchor="middle" className="fill-brand text-[10px] font-semibold">
                {flow.n} on road · {Math.round(flow.liters / 100) / 10}k L
              </text>
            )}
          </g>
        );
      })}
      {depots.map((d) => {
        const y = dPos.get(d.id) ?? 0;
        return (
          <g key={d.id} data-testid={`node-${d.id}`}>
            <rect x={DX - 70} y={y - 34} width={140} height={68} rx={10} fill="#1A1A1E"
              stroke={d.status === "CONSTRAINED" ? "#C9B069" : "#3A3A43"} strokeWidth={1.5} />
            <text x={DX} y={y - 18} textAnchor="middle" className="fill-ink text-[12px] font-semibold">{shortName(d.name)} depot</text>
            {FUELS.map((f, i) => (
              <rect key={f} x={DX - 58} y={y - 8 + i * 12} height={7} rx={2} fill={FUEL_FILL[f]}
                width={Math.max(1, (116 * d.inventory[f]) / d.capacity[f])} />
            ))}
          </g>
        );
      })}
      {stations.map((s) => {
        const y = sPos.get(s.id) ?? 0;
        const lvl = worst.get(s.id) ?? "LOW";
        return (
          <g key={s.id} data-testid={`node-${s.id}`}>
            <rect x={SX} y={y - 30} width={170} height={60} rx={10} fill="#1A1A1E"
              stroke={s.status === "OUTAGE" ? "#4A4A53" : (lvl === "LOW" ? "#3A3A43" : RISK_STROKE[lvl])} strokeWidth={lvl === "LOW" ? 1.5 : 3} />
            <text x={SX + 8} y={y - 14} className="fill-ink text-[12px] font-semibold">{shortName(s.name)}</text>
            <text x={SX + 162} y={y - 14} textAnchor="end" className="text-[10px] font-semibold"
              fill={s.status === "OUTAGE" ? "#6A6A74" : RISK_STROKE[lvl]}>
              {s.status === "OUTAGE" ? "OUTAGE" : lvl}{s.demand_multiplier !== 1 ? ` ×${s.demand_multiplier.toFixed(1)}` : ""}
            </text>
            {FUELS.map((f, i) => (
              <g key={f}>
                <rect x={SX + 8} y={y - 6 + i * 11} width={154} height={7} rx={3} fill="#0E0E10" />
                <rect x={SX + 8} y={y - 6 + i * 11} height={7} rx={2} fill={FUEL_FILL[f]}
                  width={Math.max(1, (154 * s.inventory[f]) / s.capacity[f])} />
              </g>
            ))}
          </g>
        );
      })}
    </svg>
  );
}
