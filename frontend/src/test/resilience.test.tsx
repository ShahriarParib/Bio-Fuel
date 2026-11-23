import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import SystemHealth from "@/pages/SystemHealth";

const status = {
  overall: "Degraded", engine_mode: "DEGRADED", mode_reasons: ["intelligence: prediction service down"],
  data_tick: 120, data_age_seconds: 0.4, versions: { api: "dev" },
  components: {
    fuel_simulator: { status: "Healthy", error_rate: 0, p95_ms: 40, breaker: "CLOSED" },
    prediction_service: { status: "Down", detail: "prediction service down (breaker open); fallback forecaster active" },
    data_quality: { status: "Healthy" },
  },
};
const incidents = {
  open: 1,
  items: [{ id: 3, component: "intelligence", status: "OPEN", severity_status: "DOWN", summary: "down", opened_at: "2026-11-25T10:00:00.000+00:00",
    mitigated_at: "2026-11-25T10:00:00.000+00:00", resolved_at: null, mttr_s: null,
    timeline: [{ at: "2026-11-25T10:00:00.000+00:00", kind: "detected", text: "DOWN: down" },
               { at: "2026-11-25T10:00:00.000+00:00", kind: "mitigated", text: "built-in seasonal fallback forecaster" }] }],
};

describe("SystemHealth", () => {
  it("shows the resilience contract engaged and the incident timeline", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(new Response(
      JSON.stringify(url.includes("incidents") ? incidents : status), { status: 200, headers: { "content-type": "application/json" } }))));
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><SystemHealth /></MemoryRouter></QueryClientProvider>);
    await waitFor(() => expect(screen.getByTestId("resilience-contract")).toBeInTheDocument());
    expect(screen.getAllByText("ENGAGED")).toHaveLength(2);           // fallback model + low confidence (DEGRADED)
    expect(screen.getByTestId("mode-reasons")).toHaveTextContent("prediction service down");
    await waitFor(() => expect(screen.getByTestId("incident-3")).toHaveTextContent("mitigated"));
    vi.unstubAllGlobals();
  });
});
