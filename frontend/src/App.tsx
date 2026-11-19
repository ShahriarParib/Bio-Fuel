import { NavLink, Route, Routes, Navigate } from "react-router-dom";
import clsx from "clsx";
import { Activity, Gauge, Network, ShieldAlert, Inbox, History, Bell, FlaskConical, Bot, HeartPulse, Settings2 } from "lucide-react";
import SystemHealth from "@/pages/SystemHealth";

/** Navigation = 07_FRONTEND_DESIGN.md §1. Unbuilt pages render a placeholder naming the phase that builds them. */
const NAV = [
  { to: "/overview", label: "Overview", Icon: Gauge, phase: 3 },
  { to: "/network", label: "Network", Icon: Network, phase: 3 },
  { to: "/risk", label: "Risk", Icon: ShieldAlert, phase: 6 },
  { to: "/recommendations", label: "Recommendations", Icon: Inbox, phase: 4 },
  { to: "/decisions", label: "Decisions", Icon: History, phase: 4 },
  { to: "/alerts", label: "Alerts", Icon: Bell, phase: 6 },
  { to: "/whatif", label: "What-if", Icon: FlaskConical, phase: 8 },
  { to: "/assistant", label: "Assistant", Icon: Bot, phase: 9 },
  { to: "/health", label: "System Health", Icon: HeartPulse, phase: 0 },
  { to: "/control", label: "Control (demo)", Icon: Settings2, phase: 10 },
];

function Placeholder({ label, phase }: { label: string; phase: number }) {
  return (
    <div className="rounded-lg border border-dashed border-bg-border p-8 text-ink-muted">
      <h1 className="mb-2 text-lg font-semibold text-ink">{label}</h1>
      Built in phase {String(phase).padStart(2, "0")}.
    </div>
  );
}

export default function App() {
  return (
    <div className="flex h-full flex-col">
      <div className="bg-amber-500/90 px-4 py-0.5 text-center text-[11px] font-semibold tracking-wide text-black" data-testid="simulated-strip">
        SIMULATED ENVIRONMENT – NOT REAL FUEL INFRASTRUCTURE
      </div>
      <header className="flex h-12 items-center gap-4 border-b border-bg-border bg-bg-panel px-4">
        <Activity className="text-brand" size={20} aria-hidden />
        <span className="font-semibold">Fuel Ops Console</span>
        <span className="ml-auto text-sm text-ink-muted" data-testid="sim-clock">tick: — (phase 03)</span>
      </header>
      <div className="flex min-h-0 flex-1">
        <nav className="w-56 shrink-0 space-y-1 border-r border-bg-border bg-bg-panel p-2">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                clsx("flex items-center gap-2 rounded-md px-3 py-2 text-sm", isActive ? "bg-brand-soft text-ink" : "text-ink-muted hover:bg-bg-raised")
              }
            >
              <Icon size={16} aria-hidden /> {label}
            </NavLink>
          ))}
        </nav>
        <main className="min-w-0 flex-1 overflow-auto p-6">
          <Routes>
            <Route path="/" element={<Navigate to="/health" replace />} />
            <Route path="/health" element={<SystemHealth />} />
            {NAV.filter((n) => n.to !== "/health").map((n) => (
              <Route key={n.to} path={n.to} element={<Placeholder label={n.label} phase={n.phase} />} />
            ))}
          </Routes>
        </main>
      </div>
    </div>
  );
}
