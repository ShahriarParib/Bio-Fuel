/** Mirror of the api contracts (21_API_CONTRACTS.md). Extend here first, then use. */
export type Fuel = "DIESEL" | "PETROL" | "OCTANE";
export const FUELS: Fuel[] = ["DIESEL", "PETROL", "OCTANE"];
export type FuelMap = Record<Fuel, number>;
export type HealthStatus = "Healthy" | "Degraded" | "Down";

export interface ComponentHealth {
  status: HealthStatus;
  latency_ms?: number;
  error?: string;
  breaker?: "CLOSED" | "OPEN" | "HALF_OPEN";
  p95_ms?: number;
  error_rate?: number;
  [k: string]: unknown;
}

export interface SystemStatus {
  overall: HealthStatus;
  engine_mode?: "NORMAL" | "DEGRADED" | "SAFE_HOLD";
  components: Record<string, ComponentHealth>;
  versions: Record<string, string>;
  p95_latency_ms?: number;
  error_rate?: number;
}
