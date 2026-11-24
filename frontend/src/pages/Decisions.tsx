import { useState } from "react";
import clsx from "clsx";
import { useDecisions } from "@/api/hooks";
import type { Decision } from "@/api/types";
import { Card, Empty, ErrorBox } from "@/components/ui";
import { liters, shortId } from "@/lib/format";
import { PlainWords } from "@/components/PlainWords";

const STATUS_CLS: Record<string, string> = {
  EXECUTED: "text-state-healthy", PARTIAL: "text-risk-medium", FAILED: "text-state-down", REJECTED: "text-ink-muted",
  EXPIRED: "text-ink-faint", PENDING_APPROVAL: "text-brand",
};
const LINE_CLS: Record<string, string> = {
  ARRIVED: "bg-state-healthy/15 text-state-healthy", IN_TRANSIT: "bg-brand/15 text-brand", PENDING: "bg-brand/10 text-brand",
  SUBMITTED: "bg-brand/10 text-brand", FAILED: "bg-state-down/15 text-state-down", REJECTED_BY_SIM: "bg-state-down/15 text-state-down",
  DEFERRED: "bg-risk-medium/15 text-risk-medium", CANCELLED: "bg-bg-raised text-ink-faint", PLANNED: "bg-bg-raised text-ink-muted",
};

/** Decision history = the audit trail an operator (or a judge) inspects: who decided, what shipped, what arrived.
 *  Click a decision number to have it explained in plain words. */
export default function Decisions() {
  const { data, error, isLoading } = useDecisions();
  if (isLoading) return <div className="animate-pulse text-ink-muted">Loading…</div>;
  if (error || !data) return <ErrorBox error={error} />;
  return (
    <Card title={`Decision history (${data.total ?? data.items.length})`}>
      {data.items.length === 0 ? <Empty>No decisions yet.</Empty> : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs" data-testid="decision-table">
            <thead className="text-left text-ink-faint">
              <tr><th className="py-1">#</th><th>Tick</th><th>Status</th><th>By</th><th>Policy</th><th>Lines (status)</th><th className="text-right">Liters</th></tr>
            </thead>
            <tbody>
              {data.items.map((d) => <DecisionRow key={d.id} d={d} />)}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/** One row of history, with the plain-language explanation one click away (it is fetched only when asked for). */
function DecisionRow({ d }: { d: Decision }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="border-t border-bg-border align-top">
        <td className="py-1.5 font-mono">
          <button onClick={() => setOpen(!open)} className="text-brand hover:underline"
            title="explain this decision in plain words" data-testid={`decision-row-${d.id}`}>
            {d.id}
          </button>
        </td>
        <td>{d.tick}</td>
        <td className={clsx("font-semibold", STATUS_CLS[d.status])}>{d.status}{d.rejected_reason ? ` (${d.rejected_reason})` : ""}</td>
        <td>{d.approved_by ?? (d.status === "PENDING_APPROVAL" ? "—" : "")}</td>
        <td className="text-ink-muted">{d.policy_version}</td>
        <td>
          <div className="flex flex-wrap gap-1">
            {d.lines.map((l) => (
              <span key={l.id} className={clsx("rounded px-1.5 py-0.5 font-mono", LINE_CLS[l.status] ?? "")}
                title={l.reason}>
                {Math.round(l.quantity / 100) / 10}k {l.fuel_type.slice(0, 1)} {shortId(l.route_id)} · {l.status}
                {l.actual_arrival_tick != null ? ` @${l.actual_arrival_tick}` : ""}
              </span>
            ))}
          </div>
        </td>
        <td className="text-right">{liters(d.total_liters)}</td>
      </tr>
      {open && (
        <tr className="border-t border-bg-border/50">
          <td colSpan={7} className="pb-3"><PlainWords kind="decisions" id={d.id} /></td>
        </tr>
      )}
    </>
  );
}
