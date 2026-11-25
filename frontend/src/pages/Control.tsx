import { useState } from "react";
import { Link } from "react-router-dom";
import clsx from "clsx";
import { Check, Pause, Play, RotateCcw, SkipForward } from "lucide-react";
import { useDemo, useWorldState } from "@/api/hooks";
import { getToken } from "@/api/client";
import type { ApiError } from "@/api/client";
import { Card } from "@/components/ui";

const EVENTS: { preset: string; label: string; hint: string }[] = [
  { preset: "demand_spike", label: "Demand spike ×1.8", hint: "all stations, 6 h" },
  { preset: "route_cut", label: "Route cut Gazipur→Mirpur", hint: "8 h; re-plan via Patiya" },
  { preset: "single_route_station_cut", label: "Cut Tongi's only route", hint: "6 h; NO_FEASIBLE_ROUTES" },
  { preset: "supply_delay", label: "Supply delay +12 ticks", hint: "next shipment late" },
  { preset: "supply_shortfall", label: "Supply shortfall 50%", hint: "next arrival halved" },
  { preset: "station_outage", label: "Station outage Karnaphuli", hint: "2 h closed" },
  { preset: "combined", label: "Combined crisis", hint: "spike + route cut + delay" },
];

const FAULTS: { type: string; label: string; body: Record<string, unknown> }[] = [
  { type: "unavailable", label: "Simulator DOWN 75 s", body: { duration_seconds: 75 } },
  { type: "error_rate", label: "50% errors 60 s", body: { duration_seconds: 60, rate: 0.5 } },
  { type: "latency", label: "Latency 2.5 s, 45 s", body: { duration_seconds: 45, delay_ms: 2500 } },
  { type: "stale_data", label: "Stale data 45 s", body: { duration_seconds: 45 } },
  { type: "stream_disconnect", label: "SSE refused 60 s", body: { duration_seconds: 60 } },
];

/** Brief §23 demo story as a checklist the presenter walks through. */
const STORY: { step: string; show: string; to?: string; preset?: string }[] = [
  { step: "Normal operations", show: "Overview: KPIs green, shipments moving", to: "/overview" },
  { step: "Operator dashboard", show: "Risk heatmap, forecast quality", to: "/risk" },
  { step: "Demand starts increasing", show: "Inject the demand spike", preset: "demand_spike" },
  { step: "System detects risk", show: "Alerts: anomaly explained by the event", to: "/alerts" },
  { step: "Intelligence predicts shortage", show: "Projected stockout + probability", to: "/risk" },
  { step: "Recommendation generated", show: "Inbox card in the brief §9 format", to: "/recommendations" },
  { step: "Operator inspects", show: "Why / constraints / alternatives", to: "/recommendations" },
  { step: "Allocation simulated & approved", show: "Risk before → after, then Approve", to: "/recommendations" },
  { step: "Crisis event", show: "Inject the combined crisis", preset: "combined" },
  { step: "System adapts", show: "Re-plan on the other route, 0 failed shipments", to: "/decisions" },
  { step: "Failure injected", show: "Simulator DOWN (below) or stop the prediction service", to: "/control" },
  { step: "Monitoring detects", show: "System Health: component Down, incident opened", to: "/health" },
  { step: "Fallback activates", show: "Banner, cached state with age, fallback tag", to: "/health" },
  { step: "Operations continue", show: "Incident resolved with timeline; KPIs recovered", to: "/health" },
];
const STORY_KEY = "fsip.demoStory";

function loadDone(): number[] {
  try { return JSON.parse(window.localStorage.getItem(STORY_KEY) ?? "[]"); } catch { return []; }
}

export default function Control() {
  const { data: s } = useWorldState();
  const demo = useDemo();
  const [done, setDone] = useState<number[]>(loadDone);
  const [last, setLast] = useState<string>("");
  const canAct = !!getToken();
  const err = demo.error as ApiError | null;

  const run = (path: string, body: unknown, label: string) =>
    demo.mutate({ path, body }, { onSuccess: () => setLast(`${new Date().toLocaleTimeString()} · ${label}`) });
  const toggle = (i: number) => {
    const next = done.includes(i) ? done.filter((x) => x !== i) : [...done, i];
    setDone(next);
    try { window.localStorage.setItem(STORY_KEY, JSON.stringify(next)); } catch { /* storage blocked */ }
  };
  const btn = "rounded border border-bg-border px-3 py-1.5 text-sm hover:bg-bg-raised disabled:opacity-40";

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Control (demo)</h1>
        <span className="text-xs text-ink-muted">Injects go to the SIMULATED world through the simulator's admin API. Operator token required; every action is audit-logged.</span>
      </div>
      {!canAct && <div className="rounded border border-risk-medium/40 bg-risk-medium/10 px-3 py-2 text-sm text-risk-medium">Sign in with the operator token (top right) to use these controls.</div>}
      {err && <div className="text-sm text-state-down" data-testid="control-error">{err.code}: {err.message}</div>}
      {last && <div className="text-xs text-state-healthy" data-testid="control-last">done: {last}</div>}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Simulator clock">
          <div className="mb-2 text-sm text-ink-muted">{s ? `${s.clock_label} · tick ${s.tick} · ${s.sim_status}` : "—"}</div>
          <div className="flex flex-wrap gap-2">
            <button className={btn} disabled={!canAct} onClick={() => run("sim/run", {}, "run")} data-testid="sim-run"><Play size={14} className="inline" /> Run</button>
            <button className={btn} disabled={!canAct} onClick={() => run("sim/pause", {}, "pause")}><Pause size={14} className="inline" /> Pause</button>
            <button className={btn} disabled={!canAct} onClick={() => run("sim/step", {}, "step")}><SkipForward size={14} className="inline" /> Step</button>
            <button className={clsx(btn, "text-state-down")} disabled={!canAct} onClick={() => run("sim/reset", {}, "reset")}><RotateCcw size={14} className="inline" /> Reset world</button>
          </div>
        </Card>

        <Card title="Domain injects (simulated events)">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {EVENTS.map((e) => (
              <button key={e.preset} className={clsx(btn, "text-left")} disabled={!canAct} data-testid={`event-${e.preset}`}
                onClick={() => run("events", { preset: e.preset }, e.label)}>
                <div className="font-medium text-ink">{e.label}</div><div className="text-[11px] text-ink-faint">{e.hint}</div>
              </button>
            ))}
          </div>
        </Card>

        <Card title="Failure injects (platform faults)">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {FAULTS.map((f) => (
              <button key={f.type} className={clsx(btn, "text-left")} disabled={!canAct} data-testid={`fault-${f.type}`}
                onClick={() => run("faults", { type: f.type, ...f.body }, f.label)}>{f.label}</button>
            ))}
            <button className={clsx(btn, "text-state-healthy")} disabled={!canAct} onClick={() => run("faults/clear", {}, "faults cleared")}>Clear all faults</button>
          </div>
          <div className="mt-2 text-[11px] text-ink-faint">Container failures (prediction service, DB, Redis, engine): <code>scripts/chaos/*.sh</code>. Watch them on <Link className="text-brand" to="/health">System Health</Link>.</div>
        </Card>
      </div>

      <Card title={`Demo director: brief §23 story (${done.length}/${STORY.length})`} testId="demo-director">
        <ol className="grid gap-1 md:grid-cols-2">
          {STORY.map((st, i) => (
            <li key={i} className={clsx("flex items-center gap-2 rounded px-2 py-1 text-sm", done.includes(i) && "bg-state-healthy/10")}>
              <button onClick={() => toggle(i)} aria-label={`mark step ${i + 1}`}
                className={clsx("flex h-5 w-5 shrink-0 items-center justify-center rounded border", done.includes(i) ? "border-state-healthy bg-state-healthy text-bg" : "border-bg-border")}>
                {done.includes(i) && <Check size={12} />}
              </button>
              <span className="w-5 text-right text-ink-faint">{i + 1}</span>
              <span className="font-medium text-ink">{st.step}</span>
              <span className="text-xs text-ink-muted">{st.show}</span>
              <span className="ml-auto">
                {st.preset ? (
                  <button className="text-xs text-brand disabled:opacity-40" disabled={!canAct}
                    onClick={() => { run("events", { preset: st.preset }, st.step); if (!done.includes(i)) toggle(i); }}>inject</button>
                ) : st.to ? <Link className="text-xs text-brand" to={st.to}>open</Link> : null}
              </span>
            </li>
          ))}
        </ol>
        <button className="mt-2 text-xs text-ink-faint underline" onClick={() => { setDone([]); try { window.localStorage.removeItem(STORY_KEY); } catch { /* */ } }}>reset checklist</button>
      </Card>
    </section>
  );
}
