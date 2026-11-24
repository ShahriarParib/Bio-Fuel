import clsx from "clsx";
import { AlertTriangle, GitBranch, Timer } from "lucide-react";
import { useContingency } from "@/api/hooks";
import type { N1Finding } from "@/api/types";
import { Card, Empty } from "@/components/ui";

/** N-1 contingency: what the loss of any one asset would cost, computed before it happens.
 *
 *  Two different questions share this panel, and conflating them would be a mistake:
 *  - "what runs dry, and can I reroute in time?" (critical) - today's answer, depends on today's inventory;
 *  - "which station has only one way in?" (single point of failure) - a property of the network that stays true
 *    on a day when every tank is full, which is exactly when nobody thinks about it.
 */
export function Contingency() {
  const { data, error, isLoading } = useContingency();

  if (isLoading) return <Card title="N-1 contingency"><div className="animate-pulse text-ink-muted">Scanning…</div></Card>;
  if (error || !data) {
    return (
      <Card title="N-1 contingency">
        <Empty>No scan yet — it runs every 16 ticks once the simulation is moving.</Empty>
      </Card>
    );
  }

  const assets = [...new Map(data.findings.map((f) => [f.asset, f])).values()];
  const worstOf = (asset: string, duration: number) =>
    data.findings.find((f) => f.asset === asset && f.duration_ticks === duration);
  const scale = Math.max(1, ...data.findings.map((f) => f.extra_unmet_vs_normal));

  return (
    <Card
      title="N-1 contingency: lose any one asset"
      testId="n1"
      right={
        <span className="text-[11px] text-ink-faint">
          t={data.tick} · {data.horizon_ticks} ticks ahead · {data.computed_in_ms} ms
          {data.stale && " · on degraded data"}
        </span>
      }
    >
      <p className="mb-3 text-xs text-ink-muted">
        Each asset is removed in turn and the next {data.horizon_ticks} ticks are replayed with our replenishment
        policy reacting every tick. Colour = litres of demand that would be missed <em>beyond</em> a normal day.
        Estimates from the forecast, not predictions.
      </p>

      {data.only_working_route?.length > 0 && (
        <div className="mb-3 rounded-md border border-risk-high/40 bg-risk-high/5 p-3" data-testid="n1-today">
          <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-risk-high">
            <AlertTriangle size={13} /> Only working route right now — today's exposure, not a permanent one
          </div>
          <ul className="space-y-0.5 text-xs text-ink">
            {data.only_working_route.map((s) => (
              <li key={s.asset}><b>{s.label}</b> is the only route still open to that station; its alternatives are disrupted.</li>
            ))}
          </ul>
        </div>
      )}

      {data.single_points_of_failure.length > 0 && (
        <div className="mb-4 rounded-md border border-risk-medium/40 bg-risk-medium/5 p-3" data-testid="n1-spof">
          <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-risk-medium">
            <GitBranch size={13} /> Single supply path — true whatever today's tanks say
          </div>
          <ul className="space-y-0.5 text-xs text-ink">
            {data.single_points_of_failure.map((s) => (
              <li key={s.asset}>
                <b>{s.label}</b> is the only way fuel reaches that station.
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs" data-testid="n1-heatmap">
          <thead className="text-left text-ink-faint">
            <tr>
              <th className="py-1 pr-4">Asset lost</th>
              {data.durations.map((d) => <th key={d} className="px-3 text-center">{d} ticks</th>)}
              <th className="px-3">Runs dry in</th>
              <th className="px-3">Reroute takes</th>
            </tr>
          </thead>
          <tbody>
            {assets.map((a) => {
              const worst = data.durations
                .map((d) => worstOf(a.asset, d))
                .reduce((m, f) => (f && (!m || f.extra_unmet_vs_normal > m.extra_unmet_vs_normal) ? f : m), undefined as N1Finding | undefined);
              return (
                <tr key={a.asset} className="border-t border-bg-border">
                  <td className="py-1.5 pr-4">
                    <span className={clsx(a.sole_supply_path && "font-semibold text-risk-medium",
                                          a.only_working_route && "font-semibold text-risk-high")}>{a.label}</span>
                    <span className="ml-1 text-ink-faint">{a.kind}</span>
                  </td>
                  {data.durations.map((d) => {
                    const f = worstOf(a.asset, d);
                    const v = f?.extra_unmet_vs_normal ?? 0;
                    return (
                      <td key={d} className="px-3 py-1.5 text-center">
                        <span
                          className={clsx("inline-block min-w-[4.5rem] rounded px-2 py-0.5 font-mono",
                            f?.critical ? "bg-risk-critical/25 text-risk-critical"
                              : v > 0 ? "bg-risk-medium/20 text-risk-medium"
                                : "bg-state-healthy/10 text-state-healthy")}
                          style={v > 0 ? { opacity: 0.55 + 0.45 * (v / scale) } : undefined}
                          title={f?.advice}
                        >
                          {v > 0 ? `${v.toLocaleString()} L` : "absorbed"}
                        </span>
                      </td>
                    );
                  })}
                  <td className="px-3 text-ink-muted">{worst?.tts_hours != null ? `${worst.tts_hours} h` : "—"}</td>
                  <td className="px-3 text-ink-muted">
                    {/* rerouting is a question about a supply path; a station outage has no "other way in" */}
                    {a.kind === "station" ? "—"
                      : a.sole_supply_path ? <span className="text-risk-medium">no other route</span>
                        : a.only_working_route ? <span className="text-risk-high">alternatives cut today</span>
                          : worst?.ttr_hours != null ? `${worst.ttr_hours} h` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-4 space-y-2">
        {/* one line per asset: the same advice for 24 and 48 ticks is the same advice */}
        {[...new Map((data.top_critical.length ? data.top_critical : data.worst_assets)
          .map((f) => [f.asset, f])).values()].slice(0, 3).map((f) => (
          <div key={`${f.asset}-${f.duration_ticks}`} className="flex gap-2 text-xs">
            {f.critical
              ? <AlertTriangle size={14} className="mt-0.5 shrink-0 text-risk-critical" />
              : <Timer size={14} className="mt-0.5 shrink-0 text-ink-faint" />}
            <span className="text-ink-muted">{f.advice}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}
