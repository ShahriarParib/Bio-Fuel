import { useState } from "react";
import clsx from "clsx";
import { useAck, useAlerts } from "@/api/hooks";
import { getToken } from "@/api/client";
import { Empty, ErrorBox, SeverityBadge } from "@/components/ui";

export default function Alerts() {
  const [tab, setTab] = useState<"OPEN" | "RESOLVED">("OPEN");
  const { data, error, isLoading } = useAlerts(tab);
  const ack = useAck();
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h1 className="text-xl font-semibold">Alerts</h1>
        {(["OPEN", "RESOLVED"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx("rounded px-3 py-1 text-sm", tab === t ? "bg-brand-soft text-ink" : "text-ink-muted hover:bg-bg-raised")}>
            {t === "OPEN" ? "Open" : "Recently resolved"}
          </button>
        ))}
      </div>
      {isLoading && <div className="animate-pulse text-ink-muted">Loading…</div>}
      {error && <ErrorBox error={error} />}
      {data && data.items.length === 0 && <Empty>{tab === "OPEN" ? "Nothing needs attention." : "Nothing resolved yet."}</Empty>}
      <div className="space-y-2">
        {data?.items.map((a) => (
          <article key={a.fingerprint} className="rounded-lg border border-bg-border bg-bg-panel p-3" data-testid="alert-row">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <SeverityBadge severity={a.severity} />
              <span className="font-mono text-ink-faint">{a.category}</span>
              <span className="text-ink-faint">opened t={a.opened_tick}{a.resolved_tick != null ? ` · resolved t=${a.resolved_tick}` : ""}</span>
              {a.acknowledged_by && <span className="text-ink-muted">ack by {a.acknowledged_by}</span>}
              {tab === "OPEN" && !a.acknowledged_by && a.id > 0 && (
                <button disabled={!getToken()} onClick={() => ack.mutate(a.id)}
                  className="ml-auto rounded border border-bg-border px-2 py-0.5 hover:bg-bg-raised disabled:opacity-40">Acknowledge</button>
              )}
            </div>
            <div className="mt-1 font-medium">{a.title}</div>
            <div className="text-sm text-ink-muted">{a.message}</div>
          </article>
        ))}
      </div>
    </div>
  );
}
