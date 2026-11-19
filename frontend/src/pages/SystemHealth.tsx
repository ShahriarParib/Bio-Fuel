import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { SystemStatus } from "@/api/types";
import { StatusPill } from "@/components/StatusPill";

const LABELS: Record<string, string> = {
  backend_api: "Backend API",
  database: "Database",
  redis: "Cache (Redis)",
  fuel_simulator: "Fuel Simulator",
  prediction_service: "Prediction Service",
  decision_engine: "Decision Engine",
};

export default function SystemHealth() {
  const { data, error, isLoading } = useQuery({
    queryKey: ["system-status"],
    queryFn: () => api<SystemStatus>("/system/status"),
    refetchInterval: 3000,
  });
  if (isLoading) return <div className="animate-pulse text-ink-muted">Loading system status…</div>;
  if (error || !data) return <div className="text-state-down">System status unavailable (API unreachable). Retrying…</div>;
  return (
    <section className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">System Status</h1>
        <StatusPill status={data.overall} />
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {Object.entries(data.components).map(([key, c]) => (
          <div key={key} className="rounded-lg border border-bg-border bg-bg-panel p-4" data-testid={`component-${key}`}>
            <div className="flex items-center justify-between">
              <span className="font-medium">{LABELS[key] ?? key}</span>
              <StatusPill status={c.status} />
            </div>
            {c.latency_ms != null && <div className="mt-2 text-sm text-ink-muted">latency {c.latency_ms} ms</div>}
            {c.error && <div className="mt-1 text-sm text-state-down">{c.error}</div>}
          </div>
        ))}
      </div>
      <div className="text-xs text-ink-faint">versions: {Object.entries(data.versions).map(([k, v]) => `${k}=${v}`).join(" · ")}</div>
    </section>
  );
}
