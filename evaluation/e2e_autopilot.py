"""End-to-end decision-quality check against the RUNNING stack (simulator + engine + api).

Resets the simulator, switches the engine to autopilot, steps N ticks (waiting for the engine to finish each cycle) and
asserts the outcome with the simulator's own ground truth: service level, failed allocations, and overflow loss
(from /admin/audit: liters sent vs liters received). Used locally and as the CI quality gate.

    python evaluation/e2e_autopilot.py --ticks 192 --assert-sl 0.99
Exit code 0 = pass. RESETS THE SIMULATOR.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

SIM = os.getenv("SIM_URL", "http://localhost:8000")
ENGINE = os.getenv("ENGINE_URL", "http://localhost:8100")
API = os.getenv("API_URL", "http://localhost:18080")
TOKEN = os.getenv("API_OPERATOR_TOKEN", "change-me-operator")


def wait_for(fn, timeout=20.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {what}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=192)
    ap.add_argument("--assert-sl", type=float, default=0.99)
    ap.add_argument("--max-overflow", type=float, default=0.0)
    args = ap.parse_args()

    c = httpx.Client(timeout=10)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    c.post(f"{SIM}/admin/faults/clear")
    c.post(f"{SIM}/admin/reset").raise_for_status()
    wait_for(lambda: c.get(f"{ENGINE}/health/ready").json().get("tick") == 0, what="engine to see the reset")
    time.sleep(1.0)
    r = c.post(f"{API}/api/control/mode", json={"mode": "autopilot"}, headers=auth)
    r.raise_for_status()

    t0 = time.time()
    audit: dict[int, dict] = {}  # /admin/audit returns at most 1000 recent events: collect as we go

    def collect_audit():
        for ev in c.get(f"{SIM}/admin/audit", params={"limit": 1000}).json():
            audit[ev["id"]] = ev

    try:
        for i in range(args.ticks):
            tick = c.post(f"{SIM}/admin/step").json()["tick"]
            wait_for(lambda t=tick: c.get(f"{ENGINE}/health/ready").json().get("decision_tick", -1) >= t,
                     what=f"engine decision at tick {tick}")
            if i % 50 == 49:
                collect_audit()
        collect_audit()
    finally:
        c.post(f"{API}/api/control/mode", json={"mode": "advisory"}, headers=auth)

    m = c.get(f"{SIM}/v1/metrics").json()
    allocs = {a["id"]: a for a in c.get(f"{SIM}/v1/allocations").json()}
    overflow = 0.0
    for a in audit.values():
        if a.get("action") == "allocation.arrived" and int(a["entity_id"]) in allocs:
            q = allocs[int(a["entity_id"])]["quantity"]
            overflow += q - (a.get("metadata_json") or {}).get("received", q)
    ours = [a for a in allocs.values() if a["idempotency_key"].startswith("fsip-")]
    result = {
        "ticks": args.ticks, "wall_seconds": round(time.time() - t0, 1),
        "service_level": m["service_level"], "unmet_liters": round(m["unmet_demand_liters"]),
        "allocation_failures": m["allocation_failures"], "allocations": len(ours),
        "shipped_liters": round(sum(a["quantity"] for a in ours)), "overflow_liters": round(overflow, 1),
    }
    print(json.dumps(result))
    ok = (m["service_level"] >= args.assert_sl and m["allocation_failures"] == 0
          and overflow <= args.max_overflow and len(ours) > 0)
    print("E2E PASS" if ok else "E2E FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
