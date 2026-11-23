"""Generate the provisioned Grafana dashboards (06 §2.4) from one place.

    python observability/grafana/gen_dashboards.py      # writes observability/grafana/dashboards/*.json

Dashboards are code: every panel is reviewed in git, and the same helpers keep units, colours and layout consistent.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dashboards"
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}


class Board:
    def __init__(self, uid: str, title: str, description: str) -> None:
        self.uid, self.title, self.description = uid, title, description
        self.panels: list[dict] = []
        self.x = self.y = 0
        self.row_h = 0

    def _place(self, w: int, h: int) -> dict:
        if self.x + w > 24:
            self.x, self.y = 0, self.y + self.row_h
            self.row_h = 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def row(self, title: str) -> None:
        if self.x:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        self.panels.append({"type": "row", "title": title, "collapsed": False,
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "id": len(self.panels) + 1})
        self.y += 1

    def add(self, kind: str, title: str, targets: list[dict], w: int = 8, h: int = 7, unit: str | None = None,
            description: str = "", thresholds: list[tuple[float | None, str]] | None = None, **extra) -> None:
        defaults: dict = {"unit": unit} if unit else {}
        if thresholds:
            defaults["thresholds"] = {"mode": "absolute",
                                      "steps": [{"value": v, "color": c} for v, c in thresholds]}
            defaults["color"] = {"mode": "thresholds"}
        panel = {"id": len(self.panels) + 1, "type": kind, "title": title, "description": description,
                 "gridPos": self._place(w, h), "datasource": targets[0].get("datasource", PROM),
                 "targets": [{"refId": chr(65 + i), **t} for i, t in enumerate(targets)],
                 "fieldConfig": {"defaults": defaults, "overrides": []}, "options": {}}
        if kind == "timeseries":
            panel["fieldConfig"]["defaults"]["custom"] = {"lineWidth": 2, "fillOpacity": 8, "showPoints": "never"}
            panel["options"] = {"legend": {"displayMode": "list", "placement": "bottom"},
                                "tooltip": {"mode": "multi"}}
        if kind == "stat":
            panel["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background",
                                "graphMode": "area", "textMode": "value_and_name"}
        panel.update(extra)
        self.panels.append(panel)

    def json(self) -> dict:
        return {"uid": self.uid, "title": self.title, "description": self.description, "tags": ["fsip"],
                "timezone": "browser", "schemaVersion": 39, "version": 1, "refresh": "5s",
                "time": {"from": "now-30m", "to": "now"}, "panels": self.panels,
                "annotations": {"list": [{
                    "name": "Engine mode changes", "datasource": PROM, "enable": True, "iconColor": "orange",
                    "expr": "changes(fsip_engine_mode{mode=\"NORMAL\"}[1m]) > 0", "step": "15s",
                    "titleFormat": "engine mode changed"}]},
                "links": [{"title": "FSIP dashboards", "type": "dashboards", "tags": ["fsip"], "asDropdown": True}]}


def q(expr: str, legend: str = "") -> dict:
    return {"expr": expr, "legendFormat": legend, "datasource": PROM}


GOOD, WARN, BAD = "green", "orange", "red"


def ops() -> Board:
    b = Board("fsip-ops", "FSIP - Operations Overview",
              "Is fuel reaching people? Simulator ground truth and what the engine is doing about it. SIMULATED.")
    b.row("Service")
    b.add("stat", "Fuel service level", [q("fsip_service_level", "service level")], w=6, h=5, unit="percentunit",
          thresholds=[(None, BAD), (0.97, WARN), (0.99, GOOD)], description="served / demanded (simulator /v1/metrics)")
    b.add("stat", "Domain SLO budget left", [q('fsip_slo_budget_remaining{slo="fuel_service_level"}', "budget")],
          w=6, h=5, unit="percentunit", thresholds=[(None, BAD), (0.25, WARN), (0.5, GOOD)],
          description="< 25 % → the engine forces autopilot off (error-budget policy)")
    b.add("stat", "Unmet demand prevented", [q("fsip_unmet_prevented_liters", "litres")], w=6, h=5, unit="litre",
          thresholds=[(None, "blue")], description="exact do-nothing counterfactual (Impact Meter)")
    b.add("stat", "Pending recommendations", [q("fsip_pending_recommendations", "waiting for a human")], w=6, h=5,
          thresholds=[(None, GOOD), (3, WARN), (6, BAD)])
    b.add("timeseries", "Service level over time", [q("fsip_service_level", "service level")], w=12, unit="percentunit")
    b.add("timeseries", "Unmet litres (cumulative) vs counterfactual",
          [q("fsip_sim_unmet_liters", "unmet with the platform"), q("fsip_counterfactual_unmet_liters", "unmet if nobody acted")],
          w=12, unit="litre")
    b.row("Stock")
    b.add("heatmap", "Stockout probability (12 h) by station × fuel",
          [q("fsip_stockout_prob", "{{station}} {{fuel}}")], w=12, h=8, unit="percentunit",
          options={"calculate": False, "yAxis": {"axisPlacement": "left"}, "color": {"scheme": "Reds", "mode": "scheme"},
                   "cellGap": 1})
    b.add("bargauge", "Station inventory now", [q("fsip_station_inventory_liters", "{{station}} {{fuel}}")], w=12, h=8,
          unit="litre", options={"orientation": "horizontal", "displayMode": "gradient",
                                  "reduceOptions": {"calcs": ["lastNotNull"]}})
    b.add("timeseries", "Station inventory", [q("fsip_station_inventory_liters", "{{station}} {{fuel}}")], w=12,
          unit="litre")
    b.add("timeseries", "Depot inventory", [q("fsip_depot_inventory_liters", "{{depot}} {{fuel}}")], w=12, unit="litre")
    b.add("timeseries", "In-flight litres (our shipments on the way)",
          [q("sum by (station) (fsip_inflight_liters)", "{{station}}")], w=12, unit="litre")
    b.add("timeseries", "Network cover (days of forecast demand)", [q("fsip_network_cover_days", "{{fuel}}")], w=12,
          unit="d")
    b.row("Decisions")
    b.add("timeseries", "Decisions per hour by policy", [q("sum by (policy) (rate(fsip_decisions_total[5m])) * 3600",
                                                           "{{policy}}")], w=8)
    b.add("timeseries", "Allocations submitted / failed",
          [q("sum(rate(fsip_allocations_submitted_total[5m])) * 3600", "submitted /h"),
           q("sum(rate(fsip_allocation_failures_total[5m])) * 3600", "failed /h")], w=8)
    b.add("timeseries", "Open alerts by severity", [q("fsip_alerts_open", "{{severity}}")], w=8)
    return b


def health() -> Board:
    b = Board("fsip-health", "FSIP - Service Health",
              "RED metrics, dependencies as measured from real traffic, engine mode, containers.")
    b.row("Engine state")
    b.add("stat", "Engine mode (now)", [{**q("fsip_engine_mode == 1", "{{mode}}"), "instant": True, "range": False}],
          w=6, h=5,
          options={"reduceOptions": {"calcs": ["lastNotNull"]}, "textMode": "name", "colorMode": "background"},
          thresholds=[(None, "blue")])
    b.add("state-timeline", "Component status (2 up, 1 degraded, 0 down)", [q("fsip_component_status", "{{component}}")],
          w=18, h=5, thresholds=[(None, BAD), (1, WARN), (2, GOOD)],
          options={"showValue": "never", "mergeValues": True, "rowHeight": 0.8})
    b.row("API (RED)")
    b.add("timeseries", "API request rate by route", [q('sum by (route) (rate(http_requests_total{service="api"}[1m]))',
                                                        "{{route}}")], w=8, unit="reqps")
    b.add("timeseries", "API latency p50 / p95 / p99", [
        q('histogram_quantile(0.5, sum by (le) (rate(http_request_duration_seconds_bucket{service="api"}[1m])))', "p50"),
        q('histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{service="api"}[1m])))', "p95"),
        q('histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{service="api"}[1m])))', "p99")],
        w=8, unit="s")
    b.add("timeseries", "API 5xx ratio", [q('(sum(rate(http_requests_total{service="api",status=~"5.."}[1m])) or vector(0)) / '
                                            'clamp_min(sum(rate(http_requests_total{service="api"}[1m])), 1e-9)', "5xx")],
          w=8, unit="percentunit")
    b.row("Simulator (real traffic)")
    b.add("timeseries", "Simulator call latency p95 by endpoint", [q(
        "histogram_quantile(0.95, sum by (le, endpoint) (rate(fsip_sim_request_duration_seconds_bucket[1m])))",
        "{{endpoint}}")], w=8, unit="s")
    b.add("timeseries", "Simulator errors by code", [q("sum by (code) (rate(fsip_sim_errors_total[1m]))", "{{code}}")],
          w=8, unit="reqps")
    b.add("state-timeline", "Circuit breakers (0 closed, 1 open, 2 half-open)", [q("fsip_breaker_state", "{{name}}")],
          w=8, thresholds=[(None, GOOD), (1, BAD), (2, WARN)], options={"showValue": "never"})
    b.add("timeseries", "Ticks behind the simulator", [q("fsip_ticks_behind", "ticks behind")], w=8)
    b.add("timeseries", "SSE connected / reconnects", [q("fsip_sse_connected", "connected"),
                                                        q("increase(fsip_sse_reconnects_total[5m])", "reconnects /5m")], w=8)
    b.add("timeseries", "Sync cycle duration p95", [q(
        "histogram_quantile(0.95, sum by (le) (rate(fsip_cycle_duration_seconds_bucket[1m])))", "p95")], w=8, unit="s")
    b.row("Storage and containers")
    b.add("timeseries", "History writes buffered / dropped", [q("fsip_db_buffered_writes", "buffered"),
                                                               q("increase(fsip_db_dropped_writes_total[5m])", "dropped /5m")],
          w=8)
    b.add("timeseries", "Container CPU (cores)", [q(
        'sum by (container_label_com_docker_compose_service) (rate(container_cpu_usage_seconds_total[1m]))',
        "{{container_label_com_docker_compose_service}}")], w=8)
    b.add("timeseries", "Container memory", [q(
        'sum by (container_label_com_docker_compose_service) (container_memory_working_set_bytes)',
        "{{container_label_com_docker_compose_service}}")], w=8, unit="bytes")
    b.row("SLOs (1 h window)")
    b.add("bargauge", "Error budget remaining", [q("slo:error_budget_remaining:ratio", "{{slo}}")], w=12, h=6,
          unit="percentunit", thresholds=[(None, BAD), (0.25, WARN), (0.5, GOOD)],
          options={"orientation": "horizontal", "displayMode": "lcd", "reduceOptions": {"calcs": ["lastNotNull"]}})
    b.add("timeseries", "Burn rate (1 = spending the budget exactly)", [
        q("slo:api_availability:error_ratio_rate5m / 0.005", "api availability"),
        q("slo:fuel_service_level:error_ratio_rate5m / 0.01", "fuel service level"),
        q("slo:decision_freshness:error_ratio_rate5m / 0.01", "decision freshness")], w=12, h=6)
    return b


def intelligence() -> Board:
    b = Board("fsip-intel", "FSIP - Intelligence",
              "Forecast quality against the noise floor, interval coverage, fallbacks, solver time.")
    b.add("stat", "Forecast WAPE", [q("fsip_forecast_wape", "WAPE")], w=6, h=5, unit="percentunit",
          thresholds=[(None, GOOD), (0.1, WARN), (0.2, BAD)])
    b.add("stat", "Noise floor (best possible WAPE)", [q("fsip_forecast_noise_floor_wape", "floor")], w=6, h=5,
          unit="percentunit", thresholds=[(None, "blue")])
    b.add("stat", "80 % interval coverage", [q("fsip_forecast_interval_coverage", "coverage")], w=6, h=5,
          unit="percentunit", thresholds=[(None, BAD), (0.7, WARN), (0.75, GOOD)])
    b.add("stat", "Primary model serving", [q("fsip_forecast_remote", "remote model")], w=6, h=5,
          thresholds=[(None, WARN), (1, GOOD)], description="0 = built-in fallback forecaster")
    b.add("timeseries", "WAPE vs noise floor", [q("fsip_forecast_wape", "WAPE"),
                                                 q("fsip_forecast_noise_floor_wape", "noise floor")], w=12,
          unit="percentunit")
    b.add("timeseries", "Interval coverage (target 80 %)", [q("fsip_forecast_interval_coverage", "coverage")], w=12,
          unit="percentunit")
    b.add("timeseries", "Fallback activations", [q("sum by (component, reason) (increase(fsip_fallback_activations_total[5m]))",
                                                   "{{component}}: {{reason}}")], w=12)
    b.add("timeseries", "Decision (solver) time p95 by policy", [q(
        "histogram_quantile(0.95, sum by (le, policy) (rate(fsip_decision_plan_seconds_bucket[5m])))", "{{policy}}")],
        w=12, unit="s")
    b.add("timeseries", "Prediction service: request time p95", [q(
        "histogram_quantile(0.95, sum by (le) (rate(fsip_forecast_request_seconds_bucket[5m])))", "engine → intelligence"),
        q("histogram_quantile(0.95, sum by (le) (rate(fsip_forecast_compute_seconds_bucket[5m])))", "compute")],
        w=12, unit="s")
    b.add("timeseries", "Alerts opened by category", [q("sum by (category) (increase(fsip_alerts_opened_total[15m]))",
                                                        "{{category}}")], w=12)
    return b


def logs() -> Board:
    b = Board("fsip-logs", "FSIP - Logs & Incidents", "Structured JSON logs from Loki; incidents and mode changes.")
    b.add("timeseries", "Incidents opened by component", [q("sum by (component) (increase(fsip_incidents_total[5m]))",
                                                            "{{component}}")], w=12, h=6)
    b.add("timeseries", "Log volume by level", [{"expr": 'sum by (level) (count_over_time({service=~".+"}[1m]))',
                                                 "legendFormat": "{{level}}", "datasource": LOKI}], w=12, h=6)
    for title, expr in [
        ("Decisions and allocations", '{service="engine"} |~ "decision\\\\.|allocation\\\\."'),
        ("Incidents, fallbacks, breakers, mode changes",
         '{service="engine"} |~ "incident\\\\.|fallback\\\\.|breaker\\\\.|mode_changed|reset\\\\.detected"'),
        ("Warnings and errors (all services)", '{level=~"warning|error"}'),
    ]:
        b.add("logs", title, [{"expr": expr, "datasource": LOKI}], w=24, h=9,
              options={"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True})
    return b


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("fsip-*.json"):
        old.unlink()
    for board, name in ((ops(), "fsip-ops"), (health(), "fsip-service-health"), (intelligence(), "fsip-intelligence"),
                        (logs(), "fsip-logs")):
        (OUT / f"{name}.json").write_text(json.dumps(board.json(), indent=1) + "\n", encoding="utf-8")
        print(f"{name}.json: {len(board.panels)} panels")
