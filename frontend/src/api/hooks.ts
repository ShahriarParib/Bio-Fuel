/** Live data hooks. Polling keeps the console correct even when the event stream or the engine is down; the api
 *  serves cached state (ETag) so a 1 s poll is cheap. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, setToken } from "./client";
import type { Alert, AssistantStatus, Decision, Explained, Incident, ListOf, ShiftSummary, SystemStatus, WorldState } from "./types";

const TOKEN_KEY = "fsip.operatorToken";

export function loadToken(): string | null {
  try {
    const t = window.localStorage.getItem(TOKEN_KEY);
    setToken(t);
    return t;
  } catch {
    return null;
  }
}

export function saveToken(t: string | null) {
  setToken(t);
  try {
    if (t) window.localStorage.setItem(TOKEN_KEY, t);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage blocked: keep it in memory only */
  }
}

export const useWorldState = () =>
  useQuery({ queryKey: ["state"], queryFn: () => api<WorldState>("/state"), refetchInterval: 1000 });

export const useRecommendations = () =>
  useQuery({ queryKey: ["recommendations"], queryFn: () => api<ListOf<Decision>>("/recommendations"), refetchInterval: 1500 });

export const useDecisions = () =>
  useQuery({ queryKey: ["decisions"], queryFn: () => api<ListOf<Decision>>("/decisions?limit=100"), refetchInterval: 2000 });

export const useAlerts = (status = "OPEN") =>
  useQuery({ queryKey: ["alerts", status], queryFn: () => api<ListOf<Alert>>(`/alerts?status=${status}`), refetchInterval: 1500 });

export const useSystemStatus = () =>
  useQuery({ queryKey: ["system-status"], queryFn: () => api<SystemStatus>("/system/status"), refetchInterval: 3000 });

export const useIncidents = () =>
  useQuery({ queryKey: ["incidents"], queryFn: () => api<{ items: Incident[]; open: number }>("/incidents"), refetchInterval: 2000 });

/** Plain-language explanation, fetched only when the operator asks for it (it may cost an LLM call).
 *  The api caches by content, so re-opening the same card is free; we keep it in the query cache too. */
export const useExplain = (kind: "decisions" | "incidents", id: number, enabled: boolean,
                          what: "explain" | "postmortem" = "explain") =>
  useQuery({
    queryKey: ["explain", kind, id, what],
    queryFn: () => api<Explained>(`/${kind}/${id}/${what}`),
    enabled,
    staleTime: 10 * 60 * 1000,
    retry: false,
  });

/** The handover note. Asked for explicitly: it is a paragraph a human will read once, not a live panel. */
export const useShiftSummary = (enabled: boolean, windowTicks = 96) =>
  useQuery({
    queryKey: ["shift-summary", windowTicks],
    queryFn: () => api<ShiftSummary>(`/summary?window_ticks=${windowTicks}`),
    enabled,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

export const useAssistant = () =>
  useQuery({ queryKey: ["assistant"], queryFn: () => api<AssistantStatus>("/assistant"), staleTime: 30_000 });

/** Demo Control endpoints (operator token + DEMO_CONTROLS=true on the api). */
export function useDemo() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      api<Record<string, unknown>>(`/demo/${path}`, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSettled: done,
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    for (const k of ["state", "recommendations", "decisions", "alerts", "system-status", "incidents"]) qc.invalidateQueries({ queryKey: [k] });
  };
}

export function useApprove() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: ({ id, quantities }: { id: number; quantities?: Record<string, number> }) =>
      api<Decision>(`/decisions/${id}/approve`, { method: "POST", body: JSON.stringify({ quantities, reviewed_driver: true }) }),
    onSettled: done,
  });
}

export function useReject() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: ({ id, reason_code, comment }: { id: number; reason_code: string; comment?: string }) =>
      api<Decision>(`/decisions/${id}/reject`, { method: "POST", body: JSON.stringify({ reason_code, comment: comment ?? "" }) }),
    onSettled: done,
  });
}

export function useSetMode() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: (mode: "advisory" | "autopilot") => api<{ mode: string }>("/control/mode", { method: "POST", body: JSON.stringify({ mode }) }),
    onSettled: done,
  });
}

export function usePlanNow() {
  const done = useInvalidate();
  return useMutation({ mutationFn: () => api("/plan-now", { method: "POST", body: "{}" }), onSettled: done });
}

export function useAck() {
  const done = useInvalidate();
  return useMutation({ mutationFn: (id: number) => api<Alert>(`/alerts/${id}/ack`, { method: "POST", body: "{}" }), onSettled: done });
}
