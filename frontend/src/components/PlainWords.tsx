import { useState } from "react";
import clsx from "clsx";
import { MessageSquareText, ShieldCheck, Sparkles } from "lucide-react";
import { useExplain } from "@/api/hooks";

/** Plain-language explanation of something the platform already decided.
 *
 *  The badge is the point of this component. An operator must always be able to see whether they are reading
 *  the platform's own sentence or a model's re-wording of it, and the api tells us which. Model output that
 *  contained a number the platform did not compute never reaches here - the api drops it and sends the written
 *  summary with the reason, which we show.
 */
export function PlainWords({ kind, id }: { kind: "decisions" | "incidents"; id: number }) {
  const [asked, setAsked] = useState(false);
  const { data, isFetching, error } = useExplain(kind, id, asked);

  if (!asked) {
    return (
      <button
        onClick={() => setAsked(true)}
        className="flex items-center gap-1 text-xs text-brand hover:underline"
        data-testid="explain-btn"
      >
        <MessageSquareText size={14} /> Explain in plain words
      </button>
    );
  }

  return (
    <div className="mt-1 rounded-md border border-bg-border bg-bg-raised p-3 text-xs" data-testid="explain-box">
      {isFetching && !data && <div className="text-ink-faint">Writing it out…</div>}
      {error && <div className="text-state-down">Could not build an explanation. The details above are unchanged.</div>}
      {data && (
        <>
          <p className="text-[13px] leading-5 text-ink" data-testid="explain-text">{data.text}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
            <span
              className={clsx("flex items-center gap-1 rounded px-1.5 py-0.5",
                data.source === "gemini" ? "bg-brand/15 text-brand" : "bg-bg-panel text-ink-muted")}
              data-testid="explain-source"
            >
              {data.source === "gemini"
                ? <><Sparkles size={11} /> re-worded by {data.model}</>
                : <><ShieldCheck size={11} /> the platform's own summary</>}
            </span>
            {data.source === "gemini" && (
              <span className="text-ink-faint">{data.cached ? "cached" : `${data.latency_ms} ms`}</span>
            )}
            {data.note && <span className="text-ink-faint" data-testid="explain-note">· {data.note}</span>}
            <span className="ml-auto text-ink-faint">
              numbers checked against the platform's own figures
            </span>
          </div>
        </>
      )}
    </div>
  );
}
