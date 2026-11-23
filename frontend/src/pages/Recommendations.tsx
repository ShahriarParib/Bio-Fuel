import { RefreshCw } from "lucide-react";
import { usePlanNow, useRecommendations, useWorldState } from "@/api/hooks";
import { getToken } from "@/api/client";
import { RecommendationCard } from "@/components/RecommendationCard";
import { Empty, ErrorBox } from "@/components/ui";

export default function Recommendations() {
  const { data, error, isLoading } = useRecommendations();
  const state = useWorldState();
  const planNow = usePlanNow();
  const canAct = !!getToken();
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">Recommendations</h1>
        <span className="text-sm text-ink-muted">Every line passed the shared safety checks; approval re-checks them on fresh data.</span>
        <button disabled={!canAct || planNow.isPending} onClick={() => planNow.mutate()}
          className="ml-auto flex items-center gap-1 rounded border border-bg-border px-3 py-1 text-sm hover:bg-bg-raised disabled:opacity-40">
          <RefreshCw size={14} /> Re-plan now
        </button>
      </div>
      {isLoading && <div className="animate-pulse text-ink-muted">Loading…</div>}
      {error && <ErrorBox error={error} />}
      {data && data.items.length === 0 && (
        <Empty>No recommendation is waiting. {state.data?.operating_mode === "autopilot" ? "Autopilot is executing safe ones automatically." : "The network is covered."}</Empty>
      )}
      {data?.items.map((d) => <RecommendationCard key={d.id} d={d} tick={state.data?.tick ?? d.tick} canAct={canAct} />)}
    </div>
  );
}
