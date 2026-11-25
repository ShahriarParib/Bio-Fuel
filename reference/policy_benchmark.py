"""Reference benchmark: do_nothing vs greedy vs LP (rolling horizon) against the real simulator.
Usage: SIM_URL=http://localhost:8000 python3 -I policy_benchmark.py <scenario> <do_nothing|greedy|lp|lp_fair> <ticks>
Tuned defaults (v4): LONG=1 TERM_FILL=0.9 RESERVE=0 SAFETY_TICKS=16 SAFETY_NOISE=0.15 SAFETY_W=2 (override via env).
Requires: pulp==2.9.0 (bundled CBC). Prints one JSON line of KPIs.
RESETS THE SIMULATOR.
"""
import json, os, sys, time, urllib.request, urllib.error
import pulp

B = os.environ.get("SIM_URL", "http://localhost:8000")
FUELS = ("DIESEL", "PETROL", "OCTANE")
PROFILES = {
    "urban_high": {"DIESEL": 8500, "PETROL": 10500, "OCTANE": 5600},
    "industrial": {"DIESEL": 14000, "PETROL": 4500, "OCTANE": 2200},
    "highway": {"DIESEL": 10500, "PETROL": 11000, "OCTANE": 6200},
    "regional": {"DIESEL": 7200, "PETROL": 7600, "OCTANE": 3600},
}


def hour_factor(h, p):
    if p == "industrial": return 1.55 if 6 <= h < 18 else 0.45
    if p == "highway": return 1.35 if 6 <= h < 10 or 16 <= h < 21 else 0.75
    if p == "urban_high": return 1.45 if 7 <= h < 10 or 16 <= h < 21 else 0.70
    return 1.25 if 7 <= h < 21 else 0.65


SCENARIOS = {
    "baseline": [],
    "crisis": [
        {"type": "demand_spike", "start_tick": 20, "duration_ticks": 96, "parameters": {"multiplier": 1.6}},
        {"type": "supply_shortfall", "start_tick": 30, "duration_ticks": 1, "parameters": {"factor": 0.5}},
        {"type": "route_disruption", "start_tick": 40, "duration_ticks": 48,
         "parameters": {"route_ids": ["route-patiya-karnaphuli", "route-gazipur-mirpur"]}},
        {"type": "shipment_delay", "start_tick": 50, "duration_ticks": 1, "parameters": {"delay_ticks": 24}},
        {"type": "station_outage", "start_tick": 100, "duration_ticks": 16, "parameters": {"station_ids": ["station-tongi"]}},
    ],
    "single_route_cut": [  # tongi + coxsbazar have one route each
        {"type": "route_disruption", "start_tick": 30, "duration_ticks": 40,
         "parameters": {"route_ids": ["route-gazipur-tongi", "route-patiya-coxsbazar"]}},
        {"type": "demand_spike", "start_tick": 30, "duration_ticks": 40, "parameters": {"multiplier": 1.4}},
    ],
}


def req(p, m="GET", body=None):
    r = urllib.request.Request(B + p, method=m, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(r, timeout=10))
    except urllib.error.HTTPError as e:
        return {"err": e.code, "body": e.read().decode()[:200]}


def snapshot():
    return dict(inst=req("/v1/instance"), st={s["id"]: s for s in req("/v1/stations")},
                dp={d["id"]: d for d in req("/v1/depots")}, rt={r["id"]: r for r in req("/v1/routes")},
                al=req("/v1/allocations"), ev=req("/v1/events"), sup=req("/v1/supply-arrivals"),
                regions={r["id"]: r for r in req("/v1/regions")})


def route_blocked(S, rid, tick):
    """route DISRUPTED now or a non-resolved route_disruption covering `tick`"""
    for e in S["ev"]:
        if e["type"] != "route_disruption" or e["status"] == "RESOLVED": continue
        ids = e["parameters"].get("route_ids") or []
        if (not ids or rid in ids) and e["start_tick"] <= tick < e["end_tick"]:
            return True
    return False


def multiplier_at(S, sid, tick):
    st = S["st"][sid]; m = st["demand_multiplier"]; now = S["inst"]["tick"]
    for e in S["ev"]:
        if e["type"] != "demand_spike": continue
        p = e["parameters"]; mult = p.get("multiplier", 1.5)
        sids, rids = p.get("station_ids") or [], p.get("region_ids") or []
        applies = (not sids and not rids) or sid in sids or st["region_id"] in rids
        if not applies: continue
        active_now = e["status"] == "ACTIVE"
        active_then = e["start_tick"] <= tick < e["end_tick"]
        if active_now and not active_then: m /= mult
        if not active_now and e["status"] == "SCHEDULED" and active_then: m *= mult
    return m


def demand(S, sid, f, tick):
    st = S["st"][sid]
    tm = S["inst"]["tick_minutes"]
    hour = int(((tick * tm) // 60) % 24)
    reg = S["regions"][st["region_id"]]["demand_factor"]
    return PROFILES[st["demand_profile"]][f] / (1440 / tm) * hour_factor(hour, st["demand_profile"]) * reg * multiplier_at(S, sid, tick)


def station_outage_at(S, sid, tick):
    for e in S["ev"]:
        if e["type"] != "station_outage" or e["status"] == "RESOLVED": continue
        ids = e["parameters"].get("station_ids") or []
        if (not ids or sid in ids) and e["start_tick"] <= tick < e["end_tick"]:
            return True
    return S["st"][sid]["status"] == "OUTAGE" and tick == S["inst"]["tick"]


def inflight(S):
    """list of (station, fuel, arrival_tick, qty) for our PENDING/IN_TRANSIT"""
    t = S["inst"]["tick"]; out = []
    for a in S["al"]:
        if a["status"] == "IN_TRANSIT":
            out.append((a["destination_station_id"], a["fuel_type"], a["expected_arrival_tick"], a["quantity"]))
        elif a["status"] == "PENDING":
            out.append((a["destination_station_id"], a["fuel_type"], t + S["rt"][a["route_id"]]["transit_ticks"], a["quantity"]))
    return out


# ---------------- policies ----------------
def greedy(S):
    t = S["inst"]["tick"]; lines = []
    infl = {}
    for s, f, _, q in inflight(S): infl[(s, f)] = infl.get((s, f), 0) + q
    used = {d: 0 for d in S["dp"]}
    needs = []
    for s in S["st"].values():
        if s["status"] != "OPEN": continue
        for f in FUELS:
            proj = s["inventory"][f] + infl.get((s["id"], f), 0)
            fill = proj / s["capacity"][f]
            if fill < 0.6: needs.append((fill, s["id"], f, s["capacity"][f] * 0.9 - proj))
    needs.sort()
    for _, sid, f, q in needs:
        cands = sorted([r for r in S["rt"].values() if r["destination_station_id"] == sid and r["status"] == "AVAILABLE"
                        and not route_blocked(S, r["id"], t + 1)], key=lambda r: r["transit_ticks"])
        for r in cands:
            d = S["dp"][r["source_depot_id"]]
            qq = min(q, r["max_shipment"], d["inventory"][f] - 500, d["dispatch_capacity_per_tick"] - used[d["id"]],
                     S["st"][sid]["capacity"][f] - S["st"][sid]["inventory"][f])
            if qq < 300: continue
            lines.append((r, f, round(qq, 1))); used[d["id"]] += qq; d["inventory"][f] -= qq
            break
    return lines


LONG = os.environ.get("LONG", "1") == "1"
CHECK = [2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48] + ([64, 80, 96] if LONG else [])
SLOTS = [4, 8, 12, 16, 20] + ([28, 36, 48, 64] if LONG else [])
TERM_FILL = float(os.environ.get("TERM_FILL", "0.9"))  # 0 = v1 behaviour
TERM_W = float(os.environ.get("TERM_W", "0.5"))
SAFETY_TICKS = int(os.environ.get("SAFETY_TICKS", 16))
SAFETY_NOISE = float(os.environ.get("SAFETY_NOISE", 0.15))
SAFETY_W = float(os.environ.get("SAFETY_W", 2))  # future dispatch slots (planned, not executed)


def lp(S, fair=True):
    t = S["inst"]["tick"]; H = CHECK[-1]
    prob = pulp.LpProblem("alloc", pulp.LpMinimize)
    st, dp, rt = S["st"], S["dp"], S["rt"]
    infl = inflight(S)
    # demand cumulative per (s,f) up to offset k (ticks t..t+k-1)
    cumD = {}
    for sid in st:
        for f in FUELS:
            acc, arr = 0.0, [0.0]
            for k in range(H + 40):
                tick = t + k
                acc += 0.0 if station_outage_at(S, sid, tick) else demand(S, sid, f, tick)
                arr.append(acc)
            cumD[(sid, f)] = arr
    x, y = {}, {}
    for rid, r in rt.items():
        s_ok = st[r["destination_station_id"]]["status"] == "OPEN"
        for f in FUELS:
            ok_now = s_ok and r["status"] == "AVAILABLE" and not route_blocked(S, rid, t + 1)
            room = max(0.0, st[r["destination_station_id"]]["capacity"][f] - st[r["destination_station_id"]]["inventory"][f])
            ub = min(2 * r["max_shipment"], room) if ok_now else 0
            x[rid, f] = pulp.LpVariable(f"x_{rid}_{f}", lowBound=0, upBound=ub)
            for j in SLOTS:
                okj = not route_blocked(S, rid, t + j + 1)
                y[rid, f, j] = pulp.LpVariable(f"y_{rid}_{f}_{j}", lowBound=0, upBound=2 * r["max_shipment"] if okj else 0)
    obj = []
    cover = {}
    z = pulp.LpVariable("z", lowBound=0, upBound=10) if fair else None
    for sid, s in st.items():
        for f in FUELS:
            prev_u = None
            for m in CHECK:
                arr_known = sum(q for (ss, ff, at, q) in infl if ss == sid and ff == f and at - t < m)
                arr_x = pulp.lpSum(x[rid, f] for rid, r in rt.items() if r["destination_station_id"] == sid and r["transit_ticks"] < m)
                arr_y = pulp.lpSum(y[rid, f, j] for rid, r in rt.items() for j in SLOTS
                                   if r["destination_station_id"] == sid and j + r["transit_ticks"] < m)
                u = pulp.LpVariable(f"u_{sid}_{f}_{m}", lowBound=0)
                o = pulp.LpVariable(f"o_{sid}_{f}_{m}", lowBound=0)
                inv = s["inventory"][f] + arr_known + arr_x + arr_y - cumD[(sid, f)][m] + u - o
                prob += inv >= 0
                prob += inv <= s["capacity"][f] * 0.98
                if prev_u is not None: prob += u >= prev_u
                prev_u = u
                obj += [10 * u, 40 * o]
                # rolling safety stock: next SAFETY_TICKS of demand + noise buffer (soft)
                ss = min(s["capacity"][f] * 0.85, (cumD[(sid, f)][m + SAFETY_TICKS] - cumD[(sid, f)][m]) + SAFETY_NOISE * cumD[(sid, f)][m])
                b = pulp.LpVariable(f"b_{sid}_{f}_{m}", lowBound=0)
                prob += b >= ss - inv
                obj.append(SAFETY_W * b)
                if m == CHECK[-1]:
                    # terminal safety stock: 8h of demand after horizon
                    target = min(s["capacity"][f] * 0.9, cumD[(sid, f)][H] - cumD[(sid, f)][H - 32])
                    if TERM_FILL > 0: target = max(target, TERM_FILL * s["capacity"][f])
                    sh = pulp.LpVariable(f"sh_{sid}_{f}", lowBound=0)
                    prob += sh >= target - inv
                    obj.append(TERM_W * sh)
                    if fair:
                        dem = max(1.0, cumD[(sid, f)][H] - cumD[(sid, f)][H - 32])
                        cover[(sid, f)] = (inv, dem)
    for did, d in dp.items():
        for f in FUELS:
            reserve = float(os.environ.get("RESERVE", "0")) * d["capacity"][f]
            for j in [0] + SLOTS:
                sup = sum(a["quantity"] for a in S["sup"] if a["depot_id"] == did and a["fuel_type"] == f
                          and a["status"] != "ARRIVED" and a["planned_tick"] - t <= j)
                shipped = pulp.lpSum(x[rid, f] for rid, r in rt.items() if r["source_depot_id"] == did) + \
                          pulp.lpSum(y[rid, f, jj] for rid, r in rt.items() for jj in SLOTS if r["source_depot_id"] == did and jj <= j)
                prob += shipped <= max(0.0, d["inventory"][f] - reserve) + sup
                # depot overflow (supply clipped at capacity) -> push downstream
                ov = pulp.LpVariable(f"dov_{did}_{f}_{j}", lowBound=0)
                prob += d["inventory"][f] + sup - shipped - ov <= d["capacity"][f]
                obj.append(20 * ov)
        prob += pulp.lpSum(x[rid, f] for rid, r in rt.items() if r["source_depot_id"] == did for f in FUELS) <= d["dispatch_capacity_per_tick"]
        for j in SLOTS:
            prob += pulp.lpSum(y[rid, f, j] for rid, r in rt.items() if r["source_depot_id"] == did for f in FUELS) <= d["dispatch_capacity_per_tick"]
    for (rid, f), v in x.items():
        obj.append(0.002 * rt[rid]["transit_ticks"] * v)
    for (rid, f, j), v in y.items():
        obj.append(0.002 * rt[rid]["transit_ticks"] * v)
    if fair and cover:
        # scarcity: maximise min end-cover ratio (only matters when supply is short)
        for (sid, f), (inv, dem) in cover.items():
            if st[sid]["status"] == "OPEN":
                prob += inv >= z * dem
        obj.append(-5 * z)
    prob += pulp.lpSum(obj)
    status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=5))
    lines = []
    if pulp.LpStatus[status] != "Optimal":
        return greedy(S), "fallback"
    for (rid, f), v in x.items():
        q = v.value() or 0.0
        r = rt[rid]
        while q >= 300:
            part = min(q, r["max_shipment"]); lines.append((r, f, round(part - 0.05, 1))); q -= part
    return lines, "lp"


# ---------------- runner ----------------
def run(scenario, policy, ticks):
    req("/admin/reset", "POST"); req("/admin/faults/clear", "POST")
    total0 = sum(sum(d["inventory"].values()) for d in req("/v1/depots")) + sum(sum(x["inventory"].values()) for x in req("/v1/stations"))
    for ev in SCENARIOS[scenario]: req("/admin/events", "POST", ev)
    n, errs, fallbacks, solve_t = 0, {}, 0, []
    audit = {}
    for i in range(ticks):
        if policy != "do_nothing":
            S = snapshot()
            t0 = time.time()
            if policy == "greedy":
                lines = greedy(S)
            else:
                lines, how = lp(S, fair=(policy == "lp_fair"))
                fallbacks += how == "fallback"
            solve_t.append(time.time() - t0)
            for r, f, q in lines:
                n += 1
                res = req("/v1/allocations", "POST", {"idempotency_key": f"b-{n}", "source_depot_id": r["source_depot_id"],
                                                      "destination_station_id": r["destination_station_id"], "route_id": r["id"],
                                                      "fuel_type": f, "quantity": q})
                if "err" in res:
                    k = res["body"][20:70]; errs[k] = errs.get(k, 0) + 1
        req("/admin/step", "POST")
        if i % 80 == 79 or i == ticks - 1:
            for a in req("/admin/audit?limit=1000"): audit[a["id"]] = a
    allocs = {a["id"]: a for a in req("/v1/allocations")}
    overflow = 0.0
    for a in audit.values():
        if a["action"] == "allocation.arrived":
            q = allocs[int(a["entity_id"])]["quantity"]; overflow += q - a["metadata_json"].get("received", q)
    failed_l = sum(a["quantity"] for a in allocs.values() if a["status"] == "FAILED")
    # per-station service level
    per = {}
    for sid in ("station-mirpur", "station-tongi", "station-karnaphuli", "station-coxsbazar"):
        rows = []
        lim = 2000
        rows = req(f"/v1/demand-history?station_id={sid}&limit={lim}")
        dem = sum(r["demand_liters"] for r in rows); un = sum(r["unmet_liters"] for r in rows)
        per[sid] = round(1 - un / dem, 4) if dem else 1.0
    m = req("/v1/metrics")
    sup_in = sum(a["quantity"] for a in req("/v1/supply-arrivals") if a["status"] == "ARRIVED")
    end = sum(sum(d["inventory"].values()) for d in req("/v1/depots")) + sum(sum(x["inventory"].values()) for x in req("/v1/stations"))
    inflight_end = sum(a["quantity"] for a in allocs.values() if a["status"] in ("PENDING", "IN_TRANSIT"))
    depot_clipped = round(total0 + sup_in - end - inflight_end - m["served_demand_liters"] - overflow - failed_l)
    solve_t.sort()
    return dict(scenario=scenario, policy=policy, ticks=ticks, service_level=m["service_level"],
                unmet=round(m["unmet_demand_liters"]), shipped=round(m["allocation_liters"]), failures=m["allocation_failures"],
                failed_liters=round(failed_l), overflow_liters=round(overflow), rejected=sum(errs.values()), errors=errs,
                min_station_sl_recent=min(per.values()), depot_clipped=depot_clipped, end_stock=round(end), per_station_recent=per, lp_fallbacks=fallbacks,
                p95_decide_ms=round(1000 * solve_t[int(0.95 * len(solve_t))], 1) if solve_t else None)


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], sys.argv[2], int(sys.argv[3]))))
