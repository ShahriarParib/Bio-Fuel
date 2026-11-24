import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Contingency } from "@/components/Contingency";

const finding = (over: Record<string, unknown> = {}) => ({
  asset: "route-gazipur-tongi", kind: "route", label: "Gazipur Depot → Tongi Industrial Station",
  duration_ticks: 24, unmet_liters: 0, extra_unmet_vs_normal: 0, unavoidable_unmet: 0, service_level: 1,
  first_stockout_tick: null, tts_hours: null, ttr_hours: null, critical: false, sole_supply_path: true,
  serves: "station-tongi", worst_station: null,
  advice: "Tongi Industrial Station has no second route. Pre-stock it before that route is ever cut.",
  ...over,
});

const report = {
  tick: 192, horizon_ticks: 96, durations: [24, 48], method: "closed-loop shadow projection", stale: false,
  forecast_model: "seasonal", computed_in_ms: 367,
  baseline: { unmet_liters: 0, service_level: 1, first_stockout_tick: null },
  findings: [
    finding(),
    finding({ duration_ticks: 48 }),
    finding({ asset: "route-a-mirpur", label: "Gazipur Depot → Mirpur Fuel Station", sole_supply_path: false,
              ttr_hours: 1.2, advice: "Fuel can still reach Mirpur another way in about 1.2 h." }),
    finding({ asset: "route-a-mirpur", label: "Gazipur Depot → Mirpur Fuel Station", duration_ticks: 48,
              sole_supply_path: false, ttr_hours: 1.2, extra_unmet_vs_normal: 4200, critical: true,
              tts_hours: 6.5, worst_station: "station-mirpur",
              advice: "Mirpur runs dry 6.5 h after the cut but fuel can arrive another way in 1.2 h." }),
  ],
  top_critical: [finding({ asset: "route-a-mirpur", critical: true, duration_ticks: 48, tts_hours: 6.5,
                           ttr_hours: 1.2, extra_unmet_vs_normal: 4200, sole_supply_path: false,
                           advice: "Mirpur runs dry 6.5 h after the cut but fuel can arrive another way in 1.2 h." })],
  worst_assets: [finding()],
  single_points_of_failure: [{ asset: "route-gazipur-tongi", label: "Gazipur Depot → Tongi Industrial Station",
                               serves: "station-tongi", kind: "route" }],
};

const wrap = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Contingency />
    </QueryClientProvider>,
  );

describe("Contingency (N-1)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("names the station that has only one way in, even when nothing is running dry", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
      JSON.stringify(report), { status: 200, headers: { "content-type": "application/json" } }))));
    wrap();
    await waitFor(() => expect(screen.getByTestId("n1-spof")).toBeInTheDocument());
    expect(screen.getByTestId("n1-spof")).toHaveTextContent("Gazipur Depot → Tongi Industrial Station");
    expect(screen.getByTestId("n1-spof")).toHaveTextContent("true whatever today's tanks say");
  });

  it("shows absorbed failures as absorbed rather than as a scary blank", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
      JSON.stringify(report), { status: 200, headers: { "content-type": "application/json" } }))));
    wrap();
    await waitFor(() => expect(screen.getByTestId("n1-heatmap")).toBeInTheDocument());
    expect(screen.getAllByText("absorbed").length).toBeGreaterThan(0);
    expect(screen.getByTestId("n1-heatmap")).toHaveTextContent("4,200 L");
  });

  it("says the estimate is an estimate", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
      JSON.stringify(report), { status: 200, headers: { "content-type": "application/json" } }))));
    wrap();
    await waitFor(() => expect(screen.getByTestId("n1")).toHaveTextContent("Estimates from the forecast"));
  });

  it("explains itself instead of erroring before the first scan exists", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("no", { status: 503 }))));
    wrap();
    await waitFor(() => expect(screen.getByText(/runs every 16 ticks/)).toBeInTheDocument());
  });
});
