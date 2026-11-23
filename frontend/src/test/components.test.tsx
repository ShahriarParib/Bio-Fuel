import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { NetworkGraph } from "@/components/NetworkGraph";
import { RecommendationCard } from "@/components/RecommendationCard";
import type { Decision, Depot, Route, Station } from "@/api/types";
import { shortName } from "@/lib/format";

const fm = (d: number, p: number, o: number) => ({ DIESEL: d, PETROL: p, OCTANE: o });
const depots: Depot[] = [
  { id: "depot-gazipur", name: "Gazipur Depot", region_id: "region-dhaka", status: "OPEN", dispatch_capacity_per_tick: 12000, capacity: fm(90000, 70000, 45000), inventory: fm(60000, 45000, 26000) },
  { id: "depot-patiya", name: "Patiya Depot", region_id: "region-chattogram", status: "OPEN", dispatch_capacity_per_tick: 11000, capacity: fm(85000, 65000, 40000), inventory: fm(55000, 42000, 24000) },
];
const st = (id: string, name: string, region: string): Station => ({
  id, name, region_id: region, status: "OPEN", demand_profile: "x", demand_multiplier: 1, capacity: fm(15000, 14000, 9000), inventory: fm(9000, 9000, 5000),
});
const stations = [st("station-mirpur", "Mirpur Fuel Station", "region-dhaka"), st("station-tongi", "Tongi Industrial Station", "region-dhaka"),
  st("station-karnaphuli", "Karnaphuli Highway Station", "region-chattogram"), st("station-coxsbazar", "Cox's Bazar Regional Station", "region-chattogram")];
const rt = (id: string, d: string, s: string, status: Route["status"] = "AVAILABLE"): Route =>
  ({ id, source_depot_id: d, destination_station_id: s, transit_ticks: 2, max_shipment: 7000, status });
const routes = [rt("route-gazipur-mirpur", "depot-gazipur", "station-mirpur"), rt("route-gazipur-tongi", "depot-gazipur", "station-tongi", "DISRUPTED"),
  rt("route-patiya-karnaphuli", "depot-patiya", "station-karnaphuli"), rt("route-patiya-coxsbazar", "depot-patiya", "station-coxsbazar"),
  rt("route-gazipur-karnaphuli", "depot-gazipur", "station-karnaphuli"), rt("route-patiya-mirpur", "depot-patiya", "station-mirpur")];

const wrap = (ui: ReactNode) => <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;

describe("NetworkGraph", () => {
  it("draws 6 routes, 2 depots, 4 stations and labels a disrupted route", () => {
    render(<NetworkGraph depots={depots} stations={stations} routes={routes} allocations={[]} risk={[]} />);
    expect(document.querySelectorAll('[data-testid^="edge-"]')).toHaveLength(6);
    expect(document.querySelectorAll('[data-testid^="node-"]')).toHaveLength(6);
    expect(screen.getByText("DISRUPTED")).toBeInTheDocument();
  });
});

describe("format", () => {
  it("shortens station names", () => {
    expect(shortName("Cox's Bazar Regional Station")).toBe("Cox's Bazar");
    expect(shortName("Mirpur Fuel Station")).toBe("Mirpur");
  });
});

describe("RecommendationCard", () => {
  const d: Decision = {
    id: 7, tick: 60, status: "PENDING_APPROVAL", policy: "heuristic", policy_version: "heuristic@1.0.0", total_liters: 5000,
    summary: "Ship 5,000 L DIESEL gazipur→tongi", confidence: 0.9, mode: "advisory", expires_at_tick: 68, sim_run_id: 1,
    trigger: "alert", degraded: false, fallback_reason: null, approvals: [], created_at: "",
    lines: [{ id: 1, line_no: 1, source_depot_id: "depot-gazipur", destination_station_id: "station-tongi", route_id: "route-gazipur-tongi",
      fuel_type: "DIESEL", quantity: 5000, idempotency_key: "fsip-1-7-1", status: "PLANNED", sim_allocation_id: null, sim_error_code: null,
      expected_arrival_tick: null, actual_arrival_tick: null, received_liters: null, reason: "x" }],
    impact: { method: "mc", network: { unmet_before: 2300, unmet_after: 150 }, targets: [{
      station_id: "station-tongi", station_name: "Tongi Industrial Station", fuel_type: "DIESEL", projected_stockout_hours: 6.2,
      current_inventory: 8400, expected_demand_12h: 11900, recommended: [{ liters: 5000, from: "depot-gazipur", route_id: "route-gazipur-tongi" }],
      stockout_prob_before: 0.72, stockout_prob_after: 0.19, unmet_before: 2300, unmet_after: 150, arrival_tick: 62 }] },
  };

  it("shows the brief's alert format with risk before → after", () => {
    render(wrap(<RecommendationCard d={d} tick={60} canAct={false} />));
    expect(screen.getByTestId("alert-card")).toHaveTextContent("Projected stockout: 6.2 h");
    expect(screen.getByTestId("risk-before-after")).toHaveTextContent("72%");
    expect(screen.getByTestId("risk-before-after")).toHaveTextContent("19%");
    expect(screen.getByText(/5,000 L from GAZIPUR/)).toBeInTheDocument();
  });

  it("cannot approve without an operator token", () => {
    render(wrap(<RecommendationCard d={d} tick={60} canAct={false} />));
    expect(screen.getByTestId("approve-btn")).toBeDisabled();
  });
});
