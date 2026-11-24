import clsx from "clsx";
import { Scale, TriangleAlert } from "lucide-react";
import { useFairness } from "@/api/hooks";
import { Card } from "@/components/ui";
import { pct, shortName } from "@/lib/format";

/** Who is going short, and by how much.
 *
 *  A network-wide service level of 99% can hide one station sitting dry for a day, and that is exactly the
 *  failure an operator is blamed for. So this panel never shows the headline number: it shows the distribution
 *  behind it - every station's own service level, the worst one named, and the Jain index.
 *
 *  The cover ratio answers the other half: is there even enough fuel left for the next day, whatever we do?
 */
export function Fairness({ names }: { names?: Map<string, string> }) {
  const { data, error, isLoading } = useFairness();
  if (isLoading || error || !data) return null;

  const stations = Object.entries(data.per_station ?? {}).sort((a, b) => a[1] - b[1]);
  const scarce = Object.entries(data.scarcity ?? {}).filter(([, on]) => on).map(([f]) => f);
  const label = (id: string) => names?.get(id) ?? shortName(id.replace("station-", ""));

  return (
    <Card
      title="Who is going short"
      testId="fairness"
      right={<span className="text-[11px] text-ink-faint">last {data.window_ticks} ticks · t={data.tick}</span>}
    >
      {scarce.length > 0 && (
        <div className="mb-3 flex items-start gap-2 rounded-md border border-risk-critical/40 bg-risk-critical/5 p-3"
          data-testid="scarcity-banner">
          <TriangleAlert size={15} className="mt-0.5 shrink-0 text-risk-critical" />
          <div className="text-xs">
            <div className="font-semibold text-risk-critical">
              Scarcity: {scarce.join(", ")} cannot cover the next {data.window_ticks} ticks
            </div>
            <div className="text-ink-muted">{data.policy}</div>
          </div>
        </div>
      )}

      {data.diagnosis && data.diagnosis.constraint !== "none" && (
        <div className="mb-3 rounded-md bg-bg-raised p-3 text-xs" data-testid="diagnosis">
          <span className={clsx("mr-2 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase",
            data.diagnosis.constraint === "supply" ? "bg-risk-critical/20 text-risk-critical"
              : data.diagnosis.constraint === "approval" ? "bg-brand/20 text-brand"
                : "bg-risk-medium/20 text-risk-medium")}>
            {data.diagnosis.constraint === "approval" ? "waiting on a human" : data.diagnosis.constraint}
          </span>
          <span className="text-ink-muted">{data.diagnosis.detail}</span>
        </div>
      )}

      <div className="mb-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        <Stat label="Fairness (Jain)" value={data.jain == null ? "—" : data.jain.toFixed(3)}
          tone={data.jain == null ? "neutral" : data.jain >= 0.98 ? "good" : data.jain >= 0.9 ? "warn" : "bad"}
          sub="1.0 = everyone equal" testId="jain" />
        <Stat label="Worst-served station"
          value={data.min_station == null ? "—" : pct(data.min_station, 1)}
          tone={data.min_station == null ? "neutral" : data.min_station >= 0.99 ? "good" : data.min_station >= 0.9 ? "warn" : "bad"}
          sub={data.worst_station ? label(data.worst_station) : ""} testId="min-station" />
        <Stat label="Spread" value={data.spread == null ? "—" : pct(data.spread, 1)}
          tone="neutral" sub="best minus worst" />
        <Stat label="Fuel left vs next day"
          value={fmtCover(data.cover_ratio)} tone={coverTone(data.cover_ratio)}
          sub={data.living_off_stock ? "no supply scheduled" : `supply until t=${data.supply_ends_tick}`}
          testId="cover-ratio" />
      </div>

      {stations.length > 0 && (
        <table className="w-full text-xs" data-testid="fairness-table">
          <tbody>
            {stations.map(([id, sl]) => (
              <tr key={id}>
                <td className="w-40 py-0.5 pr-2 text-ink-muted">{label(id)}</td>
                <td className="py-0.5">
                  <div className="h-2 w-full overflow-hidden rounded bg-bg-raised">
                    <div
                      className={clsx("h-full rounded",
                        sl >= 0.99 ? "bg-state-healthy" : sl >= 0.9 ? "bg-risk-medium" : "bg-risk-critical")}
                      style={{ width: `${Math.max(2, sl * 100)}%` }}
                    />
                  </div>
                </td>
                <td className="w-14 py-0.5 pl-2 text-right font-mono text-ink">{pct(sl, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="mt-3 flex items-start gap-1 text-[11px] text-ink-faint">
        <Scale size={12} className="mt-0.5 shrink-0" />
        The headline service level is an average, and an average cannot be unfair. These are the numbers that can.
      </p>
    </Card>
  );
}

function Stat({ label, value, sub, tone, testId }: {
  label: string; value: string; sub?: string; tone: "good" | "warn" | "bad" | "neutral"; testId?: string;
}) {
  return (
    <div className="rounded-md bg-bg-raised p-2" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</div>
      <div className={clsx("font-mono text-lg",
        tone === "good" ? "text-state-healthy" : tone === "warn" ? "text-risk-medium"
          : tone === "bad" ? "text-risk-critical" : "text-ink")}>{value}</div>
      {sub && <div className="text-[10px] text-ink-faint">{sub}</div>}
    </div>
  );
}

function fmtCover(cover: Record<string, number | null> | undefined): string {
  const vals = Object.values(cover ?? {}).filter((v): v is number => v != null);
  return vals.length ? `${Math.min(...vals).toFixed(2)}×` : "—";
}

function coverTone(cover: Record<string, number | null> | undefined): "good" | "warn" | "bad" | "neutral" {
  const vals = Object.values(cover ?? {}).filter((v): v is number => v != null);
  if (!vals.length) return "neutral";
  const worst = Math.min(...vals);
  return worst >= 1.5 ? "good" : worst >= 1.0 ? "warn" : "bad";
}
