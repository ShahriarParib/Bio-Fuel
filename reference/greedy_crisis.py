# Reference script from the planning analysis (ran successfully against simulator 1.0.0).
# Usage: SIM_URL=http://localhost:8000 python3 greedy_crisis.py [target_fill] [ticks]   -- RESETS the simulator!
import json, urllib.request, urllib.error, sys
import os
B=os.environ.get("SIM_URL","http://localhost:8000")
def req(p, m="GET", body=None):
    r=urllib.request.Request(B+p, method=m, data=json.dumps(body).encode() if body else None, headers={"Content-Type":"application/json"})
    try: return json.load(urllib.request.urlopen(r))
    except urllib.error.HTTPError as e: return {"err":e.code,"body":e.read().decode()[:120]}
TARGET=float(sys.argv[1]) if len(sys.argv)>1 else 0.9
TICKS=int(sys.argv[2]) if len(sys.argv)>2 else 288
req("/admin/reset","POST")
routes=req("/v1/routes"); n=0; errs={}
for ev in [{"type":"demand_spike","start_tick":20,"duration_ticks":96,"parameters":{"multiplier":1.6}},{"type":"supply_shortfall","start_tick":30,"duration_ticks":1,"parameters":{"factor":0.5}},{"type":"route_disruption","start_tick":40,"duration_ticks":48,"parameters":{"route_ids":["route-patiya-karnaphuli","route-gazipur-mirpur"]}},{"type":"shipment_delay","start_tick":50,"duration_ticks":1,"parameters":{"delay_ticks":24}},{"type":"station_outage","start_tick":100,"duration_ticks":16,"parameters":{"station_ids":["station-tongi"]}}]: print(req("/admin/events","POST",ev).get("id"))
for t in range(TICKS):
    tick=req("/v1/instance")["tick"]
    st={s["id"]:s for s in req("/v1/stations")}; dp={d["id"]:d for d in req("/v1/depots")}
    rt={r["id"]:r for r in req("/v1/routes")}
    inflight={}
    for a in req("/v1/allocations"):
        if a["status"] in("PENDING","IN_TRANSIT"):
            k=(a["destination_station_id"],a["fuel_type"]); inflight[k]=inflight.get(k,0)+a["quantity"]
    used={d:0 for d in dp}
    needs=[]
    for s in st.values():
        if s["status"]!="OPEN": continue
        for f in ("DIESEL","PETROL","OCTANE"):
            proj=s["inventory"][f]+inflight.get((s["id"],f),0)
            fill=proj/s["capacity"][f]
            if fill<0.6: needs.append((fill,s["id"],f,s["capacity"][f]*TARGET-proj))
    needs.sort()
    for fill,sid,f,q in needs:
        cands=sorted([r for r in rt.values() if r["destination_station_id"]==sid and r["status"]=="AVAILABLE"], key=lambda r:r["transit_ticks"])
        for r in cands:
            d=dp[r["source_depot_id"]]
            qq=min(q, r["max_shipment"], d["inventory"][f]-500, d["dispatch_capacity_per_tick"]-used[d["id"]])
            if qq<500: continue
            n+=1
            res=req("/v1/allocations","POST",{"idempotency_key":f"g-{n}","source_depot_id":d["id"],"destination_station_id":sid,"route_id":r["id"],"fuel_type":f,"quantity":round(qq,1)})
            if "err" in res: errs[res["body"][:60]]=errs.get(res["body"][:60],0)+1
            else: used[d["id"]]+=qq; d["inventory"][f]-=qq
            break
    req("/admin/step","POST")
    if (t+1)%96==0: print("tick",t+1,req("/v1/metrics"))
print("errors",errs)
print([ (d["id"],d["inventory"]) for d in req("/v1/depots")])
