/** Mirror of the api contracts (21_API_CONTRACTS.md). Extend here first, then use. */
export type Fuel = "DIESEL" | "PETROL" | "OCTANE";
export const FUELS: Fuel[] = ["DIESEL", "PETROL", "OCTANE"];
export type FuelMap = Record<Fuel, number>;
export type HealthStatus = "Healthy" | "Degraded" | "Down";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type Severity = "INFO" | "WARNING" | "CRITICAL";

export interface ComponentHealth {
  status: HealthStatus;
  latency_ms?: number;
  error?: string;
  breaker?: "CLOSED" | "OPEN" | "HALF_OPEN";
  p95_ms?: number;
  error_rate?: number;
  details?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface SystemStatus {
  overall: HealthStatus;
  engine_mode?: string;
  components: Record<string, ComponentHealth>;
  versions: Record<string, string>;
  p95_latency_ms?: number;
  error_rate?: number;
}

export interface Station {
  id: string; name: string; region_id: string; status: "OPEN" | "OUTAGE"; demand_profile: string;
  demand_multiplier: number; capacity: FuelMap; inventory: FuelMap;
}
export interface Depot {
  id: string; name: string; region_id: string; status: "OPEN" | "CONSTRAINED"; dispatch_capacity_per_tick: number;
  capacity: FuelMap; inventory: FuelMap;
}
export interface Route {
  id: string; source_depot_id: string; destination_station_id: string; transit_ticks: number; max_shipment: number;
  status: "AVAILABLE" | "DISRUPTED";
}
export interface Allocation {
  id: number; idempotency_key: string; source_depot_id: string; destination_station_id: string; route_id: string;
  fuel_type: Fuel; quantity: number; created_tick: number; departure_tick: number | null;
  expected_arrival_tick: number | null; actual_arrival_tick: number | null; status: string; failure_reason: string | null;
}
export interface SupplyArrival {
  id: string; depot_id: string; fuel_type: Fuel; quantity: number; planned_tick: number; actual_tick: number | null;
  status: "SCHEDULED" | "DELAYED" | "ARRIVED";
}
export interface SimEvent {
  id: number; type: string; start_tick: number; end_tick: number; status: "SCHEDULED" | "ACTIVE" | "RESOLVED";
  parameters: Record<string, unknown>;
}
export interface RiskItem {
  station_id: string; fuel_type: Fuel; level: RiskLevel; prob_12h: number; prob_lead: number;
  hours_to_stockout: number | null; inventory: number; inflight: number; capacity: number;
  expected_demand_12h: number; lead_ticks: number | null;
}
export interface Cover {
  network: Record<Fuel, { available_liters: number; demand_next_24h: number; cover_days: number | null }>;
  stations: { station_id: string; fuel_type: Fuel; hours_of_cover: number | null }[];
  supply_ends_tick: number | null;
}
export interface ForecastInfo {
  model: string; source: "intelligence" | "fallback"; reason: string | null; wape: number | null;
  coverage_80: number | null; noise_floor_wape: number | null; n: number;
}
export interface WorldState {
  sim_run_id: number; tick: number; sim_time: string; tick_minutes: number; sim_status: "PAUSED" | "RUNNING";
  clock_label: string; generated_at: string; age_seconds: number; stale: boolean; degraded: boolean;
  degraded_reasons: string[]; engine_mode: string; operating_mode: "advisory" | "autopilot"; policy: string;
  metrics: { service_level: number; served_liters: number; unmet_liters: number; allocation_liters: number;
    allocation_failures: number; overflow_liters_est: number; depot_clipped_liters_est: number };
  stations: Station[]; depots: Depot[]; routes: Route[]; events: SimEvent[]; supply_upcoming: SupplyArrival[];
  allocations_active: Allocation[]; alerts_open_count: Record<Severity, number>;
  pending_recommendations: DecisionSummary[]; risk: RiskItem[]; cover: Cover | null; forecast: ForecastInfo | null;
}

export interface DecisionLine {
  id: number; line_no: number; source_depot_id: string; destination_station_id: string; route_id: string;
  fuel_type: Fuel; quantity: number; idempotency_key: string; status: string; sim_allocation_id: number | null;
  sim_error_code: string | null; expected_arrival_tick: number | null; actual_arrival_tick: number | null;
  received_liters: number | null; reason: string;
}
export interface ImpactTarget {
  station_id: string; station_name: string; fuel_type: Fuel; projected_stockout_hours: number | null;
  current_inventory: number; expected_demand_12h: number | null;
  recommended: { liters: number; from: string; route_id: string }[];
  stockout_prob_before: number; stockout_prob_after: number; unmet_before: number; unmet_after: number;
  arrival_tick: number;
}
export interface Explanation {
  headline: string; why_at_risk: { text: string }[]; constraints: string[]; unserved?: string[];
  confidence: number; confidence_factors?: Record<string, number>; policy: string; forecast_model: string;
  degraded: boolean;
}
export interface DecisionSummary {
  id: number; tick: number; status: string; policy: string; policy_version: string; total_liters: number;
  summary: string; confidence: number; mode: string; expires_at_tick: number;
}
export interface Decision extends DecisionSummary {
  sim_run_id: number; trigger: string; degraded: boolean; fallback_reason: string | null; lines: DecisionLine[];
  approvals: { actor: string; role: string; at: string }[]; approved_by?: string; rejected_reason?: string;
  impact?: { targets: ImpactTarget[]; network: { unmet_before: number; unmet_after: number }; method: string } | null;
  explanation?: Explanation; created_at: string;
}

export interface Alert {
  id: number; fingerprint: string; category: string; severity: Severity; status: "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
  title: string; message: string; entity_type: string | null; entity_id: string | null; fuel_type: Fuel | null;
  opened_tick: number; resolved_tick: number | null; opened_at: string; acknowledged_by: string | null;
  data: Record<string, unknown>;
}
export interface ListOf<T> { items: T[]; total?: number }
