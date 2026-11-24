import { useState } from "react";
import clsx from "clsx";
import { ClipboardCheck, Copy, ShieldCheck, Sparkles } from "lucide-react";
import { useShiftSummary } from "@/api/hooks";
import { Card } from "./ui";

/** The note an operator leaves for whoever takes the next shift.
 *
 *  It is generated from the same facts the console shows, and it carries the same badge as every other
 *  explanation: an operator must be able to see whether they are about to hand over a model's words or ours.
 *  "Copy" exists because a handover note's real destination is a chat message or a logbook, not this screen.
 */
export function ShiftBrief() {
  const [asked, setAsked] = useState(false);
  const [copied, setCopied] = useState(false);
  const { data, isFetching, error, refetch } = useShiftSummary(asked);

  const copy = async () => {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(data.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked: the text is on screen and can be selected */
    }
  };

  return (
    <Card
      title="Shift handover"
      testId="shift-brief"
      right={
        <div className="flex items-center gap-2 text-xs">
          {data && (
            <button onClick={copy} className="flex items-center gap-1 text-ink-muted hover:text-ink" data-testid="shift-copy">
              {copied ? <ClipboardCheck size={13} /> : <Copy size={13} />} {copied ? "copied" : "copy"}
            </button>
          )}
          <button
            onClick={() => (asked ? refetch() : setAsked(true))}
            className="text-brand hover:underline"
            data-testid="shift-btn"
          >
            {asked ? "refresh" : "write the handover note"}
          </button>
        </div>
      }
    >
      {!asked && (
        <p className="text-sm text-ink-muted">
          One paragraph covering the last simulated day: what was served, what the platform decided, what broke and
          whether it came back, and what the next operator should watch first.
        </p>
      )}
      {asked && isFetching && !data && <div className="text-sm text-ink-faint">Writing the handover…</div>}
      {asked && error && (
        <div className="text-sm text-state-down">
          Could not build the handover note. Every figure it would contain is on this page already.
        </div>
      )}
      {data && (
        <>
          <p className="text-[13px] leading-6 text-ink" data-testid="shift-text">{data.text}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
            <span
              className={clsx("flex items-center gap-1 rounded px-1.5 py-0.5",
                data.source === "gemini" ? "bg-brand/15 text-brand" : "bg-bg-raised text-ink-muted")}
              data-testid="shift-source"
            >
              {data.source === "gemini"
                ? <><Sparkles size={11} /> re-worded by {data.model}</>
                : <><ShieldCheck size={11} /> the platform's own summary</>}
            </span>
            <span className="text-ink-faint">covering {data.window_ticks} ticks up to t={data.tick}</span>
            {data.note && <span className="text-ink-faint">· {data.note}</span>}
            <span className="ml-auto text-ink-faint">every figure checked against the platform's own</span>
          </div>
        </>
      )}
    </Card>
  );
}
