import { useState } from "react";
import { Link, NavLink, Navigate, Route, Routes } from "react-router-dom";
import clsx from "clsx";
import { Activity, Bell, Gauge, HeartPulse, History, Inbox, KeyRound, ShieldAlert } from "lucide-react";
import { loadToken, saveToken, useSetMode, useWorldState } from "@/api/hooks";
import { getToken } from "@/api/client";
import Alerts from "@/pages/Alerts";
import Decisions from "@/pages/Decisions";
import Overview from "@/pages/Overview";
import Recommendations from "@/pages/Recommendations";
import Risk from "@/pages/Risk";
import SystemHealth from "@/pages/SystemHealth";

loadToken();

const NAV = [
  { to: "/overview", label: "Overview", Icon: Gauge },
  { to: "/recommendations", label: "Recommendations", Icon: Inbox },
  { to: "/risk", label: "Risk", Icon: ShieldAlert },
  { to: "/alerts", label: "Alerts", Icon: Bell },
  { to: "/decisions", label: "Decisions", Icon: History },
  { to: "/health", label: "System Health", Icon: HeartPulse },
];

function TokenBox() {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [, force] = useState(0);
  if (getToken() && !editing)
    return (
      <button className="flex items-center gap-1 text-xs text-state-healthy" onClick={() => { saveToken(null); force((n) => n + 1); }} title="Sign out">
        <KeyRound size={14} /> operator
      </button>
    );
  return editing ? (
    <form className="flex gap-1" onSubmit={(e) => { e.preventDefault(); saveToken(value || null); setEditing(false); setValue(""); }}>
      <input type="password" autoFocus value={value} onChange={(e) => setValue(e.target.value)} placeholder="operator token"
        className="w-36 rounded border border-bg-border bg-bg px-2 py-0.5 text-xs" data-testid="token-input" />
      <button className="rounded bg-brand px-2 text-xs">OK</button>
    </form>
  ) : (
    <button className="flex items-center gap-1 text-xs text-ink-muted" onClick={() => setEditing(true)}><KeyRound size={14} /> sign in</button>
  );
}

function Header() {
  const { data: s, isError } = useWorldState();
  const setMode = useSetMode();
  const live = !!s && !isError && s.age_seconds < 10;
  const auto = s?.operating_mode === "autopilot";
  return (
    <header className="flex h-12 items-center gap-4 border-b border-bg-border bg-bg-panel px-4">
      <Activity className="text-brand" size={20} aria-hidden />
      <span className="font-semibold">Fuel Ops Console</span>
      <span className="font-mono text-sm text-ink" data-testid="sim-clock">{s ? s.clock_label : "—"}</span>
      {s && <span className="text-xs text-ink-muted">sim {s.sim_status.toLowerCase()} · run #{s.sim_run_id}</span>}
      <span className={clsx("rounded px-2 py-0.5 text-xs font-semibold",
        s?.engine_mode === "NORMAL" ? "bg-state-healthy/15 text-state-healthy" : "bg-risk-high/15 text-risk-high")} data-testid="engine-mode">
        engine {s?.engine_mode ?? "?"}
      </span>
      {s && <span className="text-xs text-ink-muted" data-testid="policy">policy: {s.policy}</span>}
      <div className="ml-auto flex items-center gap-4">
        <button disabled={!getToken() || setMode.isPending || !s}
          onClick={() => setMode.mutate(auto ? "advisory" : "autopilot")}
          className={clsx("flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold disabled:opacity-40",
            auto ? "bg-brand text-white" : "bg-bg-raised text-ink-muted")} data-testid="autopilot-toggle"
          title={auto ? "Autopilot executes safe recommendations (confidence ≥ 70%, lines ≤ 7,000 L, fresh data)" : "Every recommendation waits for a human"}>
          {auto ? "AUTOPILOT ON" : "ADVISORY (human approves)"}
        </button>
        {setMode.error && <span className="text-xs text-state-down">{(setMode.error as Error).message}</span>}
        <span className={clsx("flex items-center gap-1 text-xs", live ? "text-state-healthy" : "text-state-down")} data-testid="connection">
          <span className={clsx("h-2 w-2 rounded-full", live ? "bg-state-healthy" : "bg-state-down")} />
          {live ? "live" : s ? `data ${Math.round(s.age_seconds)} s old` : "offline"}
        </span>
        <TokenBox />
      </div>
    </header>
  );
}

function DegradedBanner() {
  const { data: s } = useWorldState();
  if (!s) return null;
  const reasons = [...s.degraded_reasons];
  if (s.stale) reasons.unshift("simulator data is STALE: autopilot paused, approvals blocked");
  if (s.forecast?.source === "fallback") reasons.push(`prediction service down: fallback model active (${s.forecast.reason ?? ""})`);
  if (!reasons.length) return null;
  return (
    <div className="border-b border-risk-high/40 bg-risk-high/10 px-4 py-1.5 text-xs text-risk-high" data-testid="degraded-banner">
      DEGRADED · {reasons.join(" · ")}
    </div>
  );
}

export default function App() {
  const { data: s } = useWorldState();
  const pending = s?.pending_recommendations.length ?? 0;
  const crit = s?.alerts_open_count?.CRITICAL ?? 0;
  return (
    <div className="flex h-full flex-col">
      <div className="bg-amber-500/90 px-4 py-0.5 text-center text-[11px] font-semibold tracking-wide text-black" data-testid="simulated-strip">
        SIMULATED ENVIRONMENT – NOT REAL FUEL INFRASTRUCTURE
      </div>
      <Header />
      <DegradedBanner />
      <div className="flex min-h-0 flex-1">
        <nav className="w-52 shrink-0 space-y-1 border-r border-bg-border bg-bg-panel p-2">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink key={to} to={to}
              className={({ isActive }) => clsx("flex items-center gap-2 rounded-md px-3 py-2 text-sm",
                isActive ? "bg-brand-soft text-ink" : "text-ink-muted hover:bg-bg-raised")}>
              <Icon size={16} aria-hidden /> {label}
              {to === "/recommendations" && pending > 0 && <span className="ml-auto rounded-full bg-brand px-1.5 text-[10px] text-white">{pending}</span>}
              {to === "/alerts" && crit > 0 && <span className="ml-auto rounded-full bg-risk-critical px-1.5 text-[10px] text-white">{crit}</span>}
            </NavLink>
          ))}
          <div className="px-3 pt-4 text-[10px] leading-4 text-ink-faint">
            Observe → Detect → Predict → Decide → Simulate → Act → Monitor → Recover.{" "}
            <Link to="/health" className="text-brand">System health</Link>
          </div>
        </nav>
        <main className="min-w-0 flex-1 overflow-auto p-6">
          <Routes>
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/overview" element={<Overview />} />
            <Route path="/recommendations" element={<Recommendations />} />
            <Route path="/risk" element={<Risk />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/decisions" element={<Decisions />} />
            <Route path="/health" element={<SystemHealth />} />
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
