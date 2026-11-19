# Reference script from the planning analysis (ran successfully against simulator 1.0.0).
# Usage: SIM_URL=http://localhost:8000 python3 baseline_do_nothing.py [target_fill] [ticks]   -- RESETS the simulator!
import json, urllib.request, time, sys
import os
B=os.environ.get("SIM_URL","http://localhost:8000")
def req(p, m="GET", body=None):
    r=urllib.request.Request(B+p, method=m, data=json.dumps(body).encode() if body else None, headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r))
req("/admin/reset","POST")
first={}
t0=time.time()
for t in range(288):
    req("/admin/step","POST")
    if t%8==0 or t>0:
        for s in req("/v1/stations"):
            for f,v in s["inventory"].items():
                if v<=1 and (s["id"],f) not in first: first[(s["id"],f)]=t+1
    if t+1 in (96,192,288): print("tick",t+1,req("/v1/metrics"))
print("step+poll time for 288 ticks: %.1fs"%(time.time()-t0))
for k,v in sorted(first.items(), key=lambda x:x[1]): print("stockout",k,"tick",v, "(hour %.1f)"%(v*15/60))
print([ (d["id"],d["inventory"]) for d in req("/v1/depots")])
