import clsx from "clsx";
import { useIncidents, useSystemStatus, useWorldState } from "@/api/hooks";
import type { ComponentHealth, Incident, SystemStatus } from "@/api/types";
import { StatusPill } from "@/components/StatusPill";
import { Card } from "@/components/ui";

const LABELS: Record<string, string> = {
  fuel_simulator: "Fuel Simulator",
  live_stream: "Live stream (SSE)",
  prediction_service: "Prediction Service",
  decision_engine: "Decision Engine",
  optimizer: "Optimizer",
  data_quality: "Data quality",
  database: "History DB",
  redis: "Cache (Redis)",
  backend_api: "Backend API",
};
const ORDER = Object.keys(LABELS);

function metricLine(c: ComponentHealth): string {
  const parts: string[] = [];
  if (typeof c.error_rate === "number") parts.push(`errors ${(c.error_rate * 100).toFixed(0)}%`);
  if (typeof c.p95_ms === "number") parts.push(`p95 ${c.p95_ms} ms`);
  if (c.breaker) parts.push(`breaker ${c.breaker}`);
  if (typeof c.buffered_writes === "number" && c.buffered_writes > 0) parts.push(`${c.buffered_writes} writes buffered`);
  return parts.join(" · ");
}

/** Brief §12: the four resilience rules, each with its live state (what the platform is doing right now). */
function ResilienceContract({ s }: { s: SystemStatus }) {
  const c = s.components;
  const bad = (k: string) => c[k] && c[k].status !== "Healthy";
  const rules = [
    { rule: "Prediction model unavailable → fallback model",
      active: bad("prediction_service"), live: bad("prediction_service") ? `ACTIVE: ${c.prediction_service?.detail ?? "fallback forecaster"}` : "primary model serving" },
    { rule: "Invalid simulator response → reject + alert",
      active: bad("data_quality"), live: bad("data_quality") ? `ACTIVE: ${c.data_quality?.detail}` : "all payloads valid" },
    { rule: "Low confidence → human review",
      active: s.engine_mode !== "NORMAL", live: s.engine_mode !== "NORMAL" ? `ACTIVE: engine ${s.engine_mode}, autopilot suspended` : "confidence gate ≥ 70% enforced on every decision" },
    { rule: "Backend / simulator down → retry, cache, degraded mode",
      active: bad("fuel_simulator") || bad("database") || bad("redis"),
      live: bad("fuel_simulator") ? `ACTIVE: ${c.fuel_simulator?.detail}; showing tick ${s.data_tick} (${Math.round(s.data_age_seconds ?? 0)} s old)`
        : bad("database") ? `ACTIVE: ${c.database?.detail}` : bad("redis") ? "ACTIVE: cache down, state read from the engine" : "all dependencies up" },
  ];
  return (
    <Card title="Resilience contract (brief §12)" testId="resilience-contract">
      <ul className="divide-y divide-bg-border">
        {rules.map((r) => (
          <li key={r.rule} className="flex items-start gap-3 py-2 text-sm">
            <span className={clsx("mt-0.5 rounded px-1.5 py-0.5 text-[10px] font-bold",
              r.active ? "bg-risk-high/20 text-risk-high" : "bg-state-healthy/15 text-state-healthy")}>
              {r.active ? "ENGAGED" : "ARMED"}
            </span>
            <div>
              <div className="font-medium text-ink">{r.rule}</div>
              <div className="text-xs text-ink-muted">{r.live}</div>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-2 text-[11px] text-ink-faint">Proof: every rule has a drill in <code>scripts/chaos/</code>; measured results in docs/evidence/resilience.md.</div>
    </Card>
  );
}

const GRAFANA = [
  { uid: "fsip-ops", title: "Operations" }, { uid: "fsip-health", title: "Service health" },
  { uid: "fsip-intel", title: "Intelligence" }, { uid: "fsip-logs", title: "Logs & incidents" },
];
const grafanaUrl = (uid: string) => `${window.location.protocol}//${window.location.hostname}:13001/d/${uid}`;

/** Domain SLO (fuel service level ≥ 99 % over one simulated day) and the error-budget policy. */
function SloPanel() {
  const { data: s } = useWorldState();
  const slo = s?.slo;
  if (!slo) return null;
  const b = Math.max(0, Math.min(1, slo.budget_remaining));
  return (
    <Card title="Domain SLO: fuel service level" testId="slo-panel">
      <div className="flex flex-wrap items-baseline gap-4 text-sm">
        <span>SLI <b className="text-ink">{slo.sli == null ? "—" : `${(slo.sli * 100).toFixed(2)} %`}</b> over {slo.window_ticks} ticks</span>
        <span className="text-ink-muted">target {(slo.target * 100).toFixed(0)} %</span>
        <span className="text-ink-muted">burn rate {slo.burn_rate}×</span>
      </div>
      <div className="mt-2 h-3 w-full overflow-hidden rounded bg-bg-raised" aria-label="error budget remaining">
        <div className={clsx("h-full", b < 0.25 ? "bg-state-down" : b < 0.5 ? "bg-risk-medium" : "bg-state-healthy")}
          style={{ width: `${b * 100}%` }} />
      </div>
      <div className="mt-1 text-xs text-ink-muted">error budget left <b className="text-ink">{(b * 100).toFixed(0)} %</b> · policy: {slo.policy}
        {slo.mode !== "enforce" && ` (${slo.mode} only)`}
        {slo.change_freeze && <b className="text-state-down"> · CHANGE FREEZE: policy switches and fault injects need a supervisor</b>}</div>
      {slo.last_action && <div className="mt-1 text-xs text-risk-high">{slo.last_action}</div>}
    </Card>
  );
}

const KIND_CLS: Record<string, string> = {
  detected: "text-state-down", mitigated: "text-risk-medium", changed: "text-ink-muted", mode: "text-brand", recovered: "text-state-healthy",
};

function IncidentRow({ inc }: { inc: Incident }) {
  return (
    <li className="py-2" data-testid={`incident-${inc.id}`}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={clsx("rounded px-1.5 py-0.5 text-[10px] font-bold",
          inc.status === "OPEN" ? "bg-state-down/15 text-state-down" : "bg-state-healthy/15 text-state-healthy")}>{inc.status}</span>
        <span className="font-medium text-ink">#{inc.id} {LABELS[inc.component] ?? inc.component}</span>
        <span className="text-xs text-ink-muted">{inc.summary}</span>
        {inc.mttr_s != null && <span className="ml-auto text-xs text-ink-muted">recovered in <b className="text-ink">{inc.mttr_s} s</b></span>}
      </div>
      <ol className="mt-1 space-y-0.5 border-l border-bg-border pl-3 font-mono text-[11px]">
        {inc.timeline.map((e, i) => (
          <li key={i}><span className="text-ink-faint">{e.at.slice(11, 23)}</span>{" "}
            <span className={clsx("font-semibold", KIND_CLS[e.kind])}>{e.kind}</span> {e.text}</li>
        ))}
      </ol>
    </li>
  );
}

export default function SystemHealth() {
  const { data, error, isLoading } = useSystemStatus();
  const inc = useIncidents();
  if (isLoading) return <div className="animate-pulse text-ink-muted">Loading system status…</div>;
  if (error || !data) return <div className="text-state-down">System status unavailable (API unreachable). Retrying… Break-glass plan: <a className="text-brand underline" href="/breakglass/latest.html">/breakglass/latest.html</a></div>;
  const keys = [...ORDER.filter((k) => k in data.components), ...Object.keys(data.components).filter((k) => !ORDER.includes(k))];
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">System Health</h1>
        <StatusPill status={data.overall} />
        <span className={clsx("rounded px-2 py-0.5 text-xs font-semibold",
          data.engine_mode === "NORMAL" ? "bg-state-healthy/15 text-state-healthy" : "bg-risk-high/15 text-risk-high")} data-testid="health-mode">
          engine {data.engine_mode}
        </span>
        <span className="text-xs text-ink-muted">measured from real traffic (errors, latency, breaker, stale header), not from /v1/health</span>
        <span className="ml-auto flex flex-wrap gap-3 text-xs">
          {GRAFANA.map((g) => <a key={g.uid} className="text-brand underline" href={grafanaUrl(g.uid)} target="_blank" rel="noreferrer">Grafana: {g.title}</a>)}
          <a className="text-brand underline" href="/breakglass/latest.html" target="_blank" rel="noreferrer">break-glass plan</a>
        </span>
      </div>
      {!!data.mode_reasons?.length && (
        <div className="rounded border border-risk-high/40 bg-risk-high/10 px-3 py-2 text-xs text-risk-high" data-testid="mode-reasons">
          {data.mode_reasons.join(" · ")}
        </div>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {keys.map((key) => {
          const c = data.components[key];
          return (
            <div key={key} className="rounded-lg border border-bg-border bg-bg-panel p-4" data-testid={`component-${key}`}>
              <div className="flex items-center justify-between">
                <span className="font-medium">{LABELS[key] ?? key}</span>
                <StatusPill status={c.status} />
              </div>
              {c.detail && <div className="mt-1 text-sm text-ink">{c.detail}</div>}
              {metricLine(c) && <div className="mt-1 text-xs text-ink-muted">{metricLine(c)}</div>}
              {c.error && <div className="mt-1 text-sm text-state-down">{c.error}</div>}
            </div>
          );
        })}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="space-y-4"><ResilienceContract s={data} /><SloPanel /></div>
        <Card title={`Incidents (${inc.data?.open ?? 0} open)`} testId="incidents">
          {inc.data?.items.length ? (
            <ul className="max-h-[28rem] divide-y divide-bg-border overflow-auto">{inc.data.items.slice(0, 12).map((i) => <IncidentRow key={i.id} inc={i} />)}</ul>
          ) : <div className="text-sm text-ink-muted">No incidents. Inject one from the Control page.</div>}
        </Card>
      </div>
      <div className="text-xs text-ink-faint">versions: {Object.entries(data.versions).map(([k, v]) => `${k}=${v}`).join(" · ")}</div>
    </section>
  );
}
