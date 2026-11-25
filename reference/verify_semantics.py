"""Verify the 4 open simulator semantics (PROGRESS.md) against the running image. RESETS the simulator."""
import json

import httpx

S = httpx.Client(base_url="http://localhost:8000", timeout=10)
K = iter(range(10_000))


def reset():
    S.post("/admin/reset").raise_for_status()
    S.post("/admin/faults/clear")


def step():
    return S.post("/admin/step").json()["tick"]


def tick():
    return S.get("/v1/instance").json()["tick"]


def alloc(route, src, dst, fuel, qty):
    body = {"idempotency_key": f"verify-{next(K)}", "source_depot_id": src, "destination_station_id": dst,
            "route_id": route, "fuel_type": fuel, "quantity": qty}
    r = S.post("/v1/allocations", json=body)
    return r.status_code, r.json()


def get_alloc(aid):
    return next(a for a in S.get("/v1/allocations").json() if a["id"] == aid)


def inv(station, fuel):
    return S.get(f"/v1/stations/{station}").json()["inventory"][fuel]


def event(etype, duration, **params):
    r = S.post("/admin/events", json={"type": etype, "start_tick": tick(), "duration_ticks": duration,
                                      "parameters": params})
    r.raise_for_status()
    return r.json()


out = {}

# 1. Arrival timing ---------------------------------------------------------------------------
reset()
t0 = tick()
code, a = alloc("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 1000)
timeline = [{"instance_tick": t0, "status": a["status"]}]
for _ in range(5):
    before = inv("station-mirpur", "DIESEL")
    t = step()
    a2 = get_alloc(a["id"])
    timeline.append({"instance_tick": t, "status": a2["status"], "departure_tick": a2.get("departure_tick"),
                     "expected_arrival_tick": a2.get("expected_arrival_tick"),
                     "actual_arrival_tick": a2.get("actual_arrival_tick"),
                     "inv_delta": round(inv("station-mirpur", "DIESEL") - before, 1)})
out["1_arrival_timing"] = {"created_tick": t0, "transit": 2, "timeline": timeline}

# 2. Route disrupted at creation --------------------------------------------------------------
reset()
event("route_disruption", 6, route_ids=["route-gazipur-tongi"])
step()  # event becomes ACTIVE
route = next(r for r in S.get("/v1/routes").json() if r["id"] == "route-gazipur-tongi")
code, body = alloc("route-gazipur-tongi", "depot-gazipur", "station-tongi", "DIESEL", 1000)
out["2_disrupted_at_creation"] = {"route_status": route["status"], "http": code,
                                  "response": body.get("detail", body.get("status"))}

# 3. Mid-transit route disruption -------------------------------------------------------------
reset()
code, a = alloc("route-patiya-coxsbazar", "depot-patiya", "station-coxsbazar", "DIESEL", 1000)
step()  # departs -> IN_TRANSIT
status_before = get_alloc(a["id"])["status"]
event("route_disruption", 8, route_ids=["route-patiya-coxsbazar"])
statuses = []
for _ in range(5):
    t = step()
    a2 = get_alloc(a["id"])
    statuses.append((t, a2["status"], a2.get("failure_reason")))
out["3_mid_transit_disruption"] = {"status_when_cut": status_before, "after": statuses}

# 4. Dispatch cap: does the previous tick's volume count? ------------------------------------
reset()
r1 = alloc("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 6000)
r2 = alloc("route-gazipur-tongi", "depot-gazipur", "station-tongi", "DIESEL", 6000)
same_tick_over = alloc("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "PETROL", 500)
step()
next_tick = alloc("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "PETROL", 5000)
out["4_dispatch_cap"] = {
    "tick0_6000+6000": [r1[0], r2[0]],
    "tick0_extra_500": [same_tick_over[0], same_tick_over[1].get("detail", {}).get("code")],
    "tick1_5000": [next_tick[0], next_tick[1].get("detail", {}).get("code") if next_tick[0] != 201 else "OK"],
}

reset()
print(json.dumps(out, indent=1, default=str))
