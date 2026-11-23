"""Policy evaluation against the RUNNING platform (not a copy of the policy): do_nothing vs heuristic vs optimizer.

For each run: reset the simulator, inject the scenario's events, put the engine in the policy under test (autopilot) or
in advisory mode with nobody approving (do nothing), step N ticks waiting for the engine after each one, then score with
the simulator's own ground truth: service level, unmet, allocation failures, overflow loss (audit: sent vs received),
worst-station service level, and fuel clipped at full depots (mass balance).

    python evaluation/harness.py --scenario crisis --policy optimizer --ticks 288
    python evaluation/harness.py --matrix --out docs/evidence/evaluation.md
RESETS THE SIMULATOR. The world is deterministic: the same scenario + policy gives the same numbers.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

SIM = os.getenv("SIM_URL", "http://localhost:8000")
ENGINE = os.getenv("ENGINE_URL", "http://localhost:8100")
API = os.getenv("API_URL", "http://localhost:18080")
OP = {"Authorization": f"Bearer {os.getenv('API_OPERATOR_TOKEN', 'change-me-operator')}"}
SUP = {"Authorization": f"Bearer {os.getenv('API_SUPERVISOR_TOKEN', 'change-me-supervisor')}"}
STATIONS = ("station-mirpur", "station-tongi", "station-karnaphuli", "station-coxsbazar")

SCENARIOS = {
    "baseline": [],
    "crisis": [  # spike ×1.6 everywhere, supply cut in half, two routes cut, supply delayed, an outage
        {"type": "demand_spike", "start_tick": 20, "duration_ticks": 96, "parameters": {"multiplier": 1.6}},
        {"type": "supply_shortfall", "start_tick": 30, "duration_ticks": 1, "parameters": {"factor": 0.5}},
        {"type": "route_disruption", "start_tick": 40, "duration_ticks": 48,
         "parameters": {"route_ids": ["route-patiya-karnaphuli", "route-gazipur-mirpur"]}},
        {"type": "shipment_delay", "start_tick": 50, "duration_ticks": 1, "parameters": {"delay_ticks": 24}},
        {"type": "station_outage", "start_tick": 100, "duration_ticks": 16,
         "parameters": {"station_ids": ["station-tongi"]}},
    ],
    "single_route_cut": [  # Tongi and Cox's Bazar have one route each
        {"type": "route_disruption", "start_tick": 30, "duration_ticks": 40,
         "parameters": {"route_ids": ["route-gazipur-tongi", "route-patiya-coxsbazar"]}},
        {"type": "demand_spike", "start_tick": 30, "duration_ticks": 40, "parameters": {"multiplier": 1.4}},
    ],
}


def run(scenario: str, policy: str, ticks: int) -> dict:
    c = httpx.Client(timeout=15)
    c.post(f"{SIM}/admin/faults/clear")
    c.post(f"{API}/api/control/mode", json={"mode": "advisory"}, headers=OP)
    c.post(f"{SIM}/admin/reset").raise_for_status()
    end = time.time() + 30
    while c.get(f"{ENGINE}/health/ready").json().get("tick") != 0 and time.time() < end:
        time.sleep(0.1)
    time.sleep(1.0)
    total0 = sum(sum(d["inventory"].values()) for d in c.get(f"{SIM}/v1/depots").json()) + \
        sum(sum(s["inventory"].values()) for s in c.get(f"{SIM}/v1/stations").json())
    for ev in SCENARIOS[scenario]:
        c.post(f"{SIM}/admin/events", json=ev).raise_for_status()
    if policy != "do_nothing":
        c.post(f"{API}/api/control/policy", json={"policy": policy}, headers=SUP).raise_for_status()
        c.post(f"{API}/api/control/mode", json={"mode": "autopilot"}, headers=OP).raise_for_status()

    audit: dict[int, dict] = {}
    plan_ms, fallbacks = [], 0
    t0 = time.time()
    try:
        for i in range(ticks):
            tick = c.post(f"{SIM}/admin/step").json()["tick"]
            end = time.time() + 30
            while True:
                r = c.get(f"{ENGINE}/health/ready").json()
                if r.get("decision_tick", -1) >= tick or time.time() > end:
                    break
                time.sleep(0.02)
            if r.get("last_plan_ms") is not None and policy != "do_nothing":
                plan_ms.append(r["last_plan_ms"])
                fallbacks += 1 if r.get("fallback_reason") else 0
            if i % 50 == 49:
                for ev in c.get(f"{SIM}/admin/audit", params={"limit": 1000}).json():
                    audit[ev["id"]] = ev
        for ev in c.get(f"{SIM}/admin/audit", params={"limit": 1000}).json():
            audit[ev["id"]] = ev
    finally:
        c.post(f"{API}/api/control/mode", json={"mode": "advisory"}, headers=OP)
        c.post(f"{API}/api/control/policy", json={"policy": "optimizer"}, headers=SUP)

    m = c.get(f"{SIM}/v1/metrics").json()
    allocs = {a["id"]: a for a in c.get(f"{SIM}/v1/allocations").json()}
    overflow = sum(allocs[int(a["entity_id"])]["quantity"] - (a.get("metadata_json") or {}).get(
        "received", allocs[int(a["entity_id"])]["quantity"]) for a in audit.values()
        if a.get("action") == "allocation.arrived" and int(a["entity_id"]) in allocs)
    overflow_events = []
    for a in audit.values():
        al = allocs.get(int(a["entity_id"])) if a.get("action") == "allocation.arrived" else None
        rec = (a.get("metadata_json") or {}).get("received") if al else None
        if al and rec is not None and al["quantity"] - rec > 0.5:
            overflow_events.append({"allocation": al["id"], "station": al["destination_station_id"], "fuel": al["fuel_type"],
                                    "created": al["created_tick"], "arrived": al.get("actual_arrival_tick"),
                                    "quantity": al["quantity"], "received": round(rec, 1)})
    failed_l = sum(a["quantity"] for a in allocs.values() if a["status"] == "FAILED")
    per = {}
    for sid in STATIONS:
        rows = c.get(f"{SIM}/v1/demand-history", params={"station_id": sid, "limit": 2000}).json()
        dem, un = sum(r["demand_liters"] for r in rows), sum(r["unmet_liters"] for r in rows)
        per[sid] = round(1 - un / dem, 4) if dem else 1.0
    sup_in = sum(a["quantity"] for a in c.get(f"{SIM}/v1/supply-arrivals").json() if a["status"] == "ARRIVED")
    end_stock = sum(sum(d["inventory"].values()) for d in c.get(f"{SIM}/v1/depots").json()) + \
        sum(sum(s["inventory"].values()) for s in c.get(f"{SIM}/v1/stations").json())
    inflight = sum(a["quantity"] for a in allocs.values() if a["status"] in ("PENDING", "IN_TRANSIT"))
    clipped = total0 + sup_in - end_stock - inflight - m["served_demand_liters"] - overflow - failed_l
    plan_ms.sort()
    return {
        "scenario": scenario, "policy": policy, "ticks": ticks, "wall_s": round(time.time() - t0, 1),
        "service_level": round(m["service_level"], 4), "unmet_liters": round(m["unmet_demand_liters"]),
        "shipped_liters": round(m["allocation_liters"]), "allocation_failures": m["allocation_failures"],
        "failed_liters": round(failed_l), "overflow_liters": round(overflow), "overflow_events": overflow_events,
        "depot_clipped_liters": round(max(0, clipped)),
        "min_station_sl": min(per.values()), "per_station_sl": per,
        "decide_ms_p50": statistics.median(plan_ms) if plan_ms else None,
        "decide_ms_p95": plan_ms[int(0.95 * (len(plan_ms) - 1))] if plan_ms else None,
        "optimizer_fallbacks": fallbacks,
    }


MATRIX = [("baseline", 288), ("crisis", 288), ("single_route_cut", 288), ("baseline", 600), ("crisis", 600)]
POLICIES = ["do_nothing", "heuristic", "optimizer"]


def write_report(rows: list[dict], out: Path) -> None:
    lines = ["# Policy evaluation (generated by `evaluation/harness.py --matrix`)", "",
             "Measured against the running platform and the official simulator image (deterministic world: re-running "
             "gives the same numbers). `do_nothing` = advisory mode with nobody approving.", "",
             "| Scenario | Ticks | Policy | Service level | Unmet L | Worst station SL | Failed shipments | Overflow L "
             "| Depot clipped L | Shipped L | Decide p95 ms |",
             "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['scenario']} | {r['ticks']} | {r['policy']} | {r['service_level']:.4f} "
                     f"| {r['unmet_liters']:,} "
                     f"| {r['min_station_sl']:.4f} | {r['allocation_failures']} | {r['overflow_liters']:,} "
                     f"| {r['depot_clipped_liters']:,} | {r['shipped_liters']:,} | {r['decide_ms_p95'] or '—'} |")
    lines += ["", "Raw results: `docs/evidence/evaluation-matrix.json`."]
    out.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="baseline", choices=list(SCENARIOS))
    ap.add_argument("--policy", default="optimizer", choices=POLICIES)
    ap.add_argument("--ticks", type=int, default=288)
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--out", default="docs/evidence/evaluation.md")
    args = ap.parse_args()
    if not args.matrix:
        print(json.dumps(run(args.scenario, args.policy, args.ticks)))
        return 0
    rows = []
    for scenario, ticks in MATRIX:
        for policy in POLICIES:
            r = run(scenario, policy, ticks)
            print(json.dumps(r), flush=True)
            rows.append(r)
    Path("docs/evidence").mkdir(parents=True, exist_ok=True)
    Path("docs/evidence/evaluation-matrix.json").write_text(json.dumps(rows, indent=1))
    write_report(rows, Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
