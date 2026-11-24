import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Fairness } from "@/components/Fairness";

const report = (over: Record<string, unknown> = {}) => ({
  tick: 824, window_ticks: 96,
  per_station: { "station-tongi": 0.9353, "station-karnaphuli": 0.9685, "station-coxsbazar": 0.9352,
                 "station-mirpur": 0.9695 },
  jain: 0.9997, min_station: 0.9352, worst_station: "station-coxsbazar", spread: 0.0343,
  scarcity: { DIESEL: false, PETROL: false, OCTANE: false },
  cover_ratio: { DIESEL: 2.75, PETROL: 1.85, OCTANE: 2.5 },
  cover_ratio_by_region: {}, supply_ends_tick: null, living_off_stock: true,
  policy: "equal cover: when fuel runs short the planner maximises the worst-served station's cover",
  diagnosis: null,
  thresholds: { enter_below: 1, exit_above: 1.1, enter_cycles: 2, exit_cycles: 4 },
  ...over,
});

function stub(body: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
    JSON.stringify(body), { status, headers: { "content-type": "application/json" } }))));
}

const wrap = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Fairness />
    </QueryClientProvider>,
  );

describe("Fairness", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("names the worst-served station rather than only an average", async () => {
    stub(report());
    wrap();
    await waitFor(() => expect(screen.getByTestId("min-station")).toHaveTextContent("93.5%"));
    expect(screen.getByTestId("min-station")).toHaveTextContent("coxsbazar");
    expect(screen.getByTestId("jain")).toHaveTextContent("1.000");
  });

  it("lists every station so one being starved cannot hide", async () => {
    stub(report());
    wrap();
    await waitFor(() => expect(screen.getByTestId("fairness-table")).toBeInTheDocument());
    for (const s of ["tongi", "karnaphuli", "coxsbazar", "mirpur"]) {
      expect(screen.getByTestId("fairness-table")).toHaveTextContent(s);
    }
  });

  it("says when the binding constraint is a human, not the fuel", async () => {
    stub(report({
      min_station: 0, worst_station: "station-tongi",
      diagnosis: { constraint: "approval",
                   detail: "fuel is available but 1 recommendation(s) are waiting for a human" },
    }));
    wrap();
    await waitFor(() => expect(screen.getByTestId("diagnosis")).toHaveTextContent("waiting on a human"));
    expect(screen.getByTestId("diagnosis")).toHaveTextContent("waiting for a human");
  });

  it("raises a scarcity banner only when a fuel is actually scarce", async () => {
    stub(report());
    const { unmount } = wrap();
    await waitFor(() => expect(screen.getByTestId("fairness")).toBeInTheDocument());
    expect(screen.queryByTestId("scarcity-banner")).toBeNull();
    unmount();
    vi.unstubAllGlobals();

    stub(report({ scarcity: { DIESEL: true, PETROL: false, OCTANE: false },
                  cover_ratio: { DIESEL: 0.7, PETROL: 1.8, OCTANE: 2.5 } }));
    wrap();
    await waitFor(() => expect(screen.getByTestId("scarcity-banner")).toHaveTextContent("DIESEL"));
  });

  it("shows the tightest fuel's cover ratio, not the most comfortable one", async () => {
    stub(report());
    wrap();
    await waitFor(() => expect(screen.getByTestId("cover-ratio")).toHaveTextContent("1.85×"));
    expect(screen.getByTestId("cover-ratio")).toHaveTextContent("no supply scheduled");
  });

  it("stays out of the way before the engine has published anything", async () => {
    stub({ detail: { code: "NOT_READY" } }, 503);
    const { container } = wrap();
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
