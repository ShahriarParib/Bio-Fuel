# Kubernetes: operational resilience evidence

Generated 2026-11-24 15:37 UTC on a single-node kind cluster from `deploy/helm/fsip`
(images `fsip/*:v1.0.0-rc1`). Reproduce with `make k8s-up && make k8s-demo`.

Docker Compose proves the platform *runs*. This file proves it *keeps* running without a human: a component
that dies is replaced, and the read path grows and shrinks with the traffic.

| # | Proof | Result |
|---|-------|--------|
| 1 | Intelligence pod killed -> platform keeps deciding on a labelled fallback, pod self-heals | **PASS** |
| 2 | 300-VU traffic spike -> api autoscales out, zero 5xx, scales back in | **PASS** |

## Proof 1 - a component dies and nobody is paged

`kubectl -n fsip delete pod fsip-intelligence-7d7d87d7df-4shc2`

| Measure | Value |
|---|---|
| Degradation visible to the operator | yes - `prediction_service: Down`, `engine_mode: DEGRADED` |
| Status requests served while the forecaster was gone | 15 |
| Status requests that failed | 0 |
| Replacement pod Ready | 7s after deletion (`fsip-intelligence-7d7d87d7df-mjgh7`) |
| Forecaster back to Healthy | 16s |
| Engine back to `NORMAL` | 16s |

The intelligence service is a *soft* dependency on purpose. When it disappears the engine switches to its
built-in seasonal forecaster, lowers decision confidence and tells the operator exactly what changed
("built-in seasonal fallback forecaster").
Kubernetes then restores the real forecaster by itself. The operator sees a labelled degradation for a few
seconds - never a blank screen, never a silent wrong answer.

Timeline (one sample per second, truncated):

```
15:31:29  +0s   prediction_service=Healthy   engine_mode=NORMAL   overall=Healthy
15:31:31  +2s   prediction_service=Healthy   engine_mode=NORMAL   overall=Healthy
15:31:32  +3s   prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:33  +4s   prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:35  +6s   prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:36  +7s   prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:37  +8s   prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:38  +9s   prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:39  +10s  prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:40  +11s  prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:41  +12s  prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:42  +13s  prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:43  +14s  prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:44  +15s  prediction_service=Down      engine_mode=DEGRADED overall=Degraded
15:31:45  +16s  prediction_service=Healthy   engine_mode=NORMAL   overall=Healthy
```

## Proof 2 - traffic arrives and the platform grows into it

k6 `spike.js`: 10 -> 300 virtual users in 5s, held 30s, through the console's nginx at `http://localhost:3080`.

| Measure | Value |
|---|---|
| api replicas before | 2 |
| api replicas at peak | 5 (autoscaler asked for 5, chart limit 6) |
| Peak CPU seen by the HPA | 150% of request (target 60%) |
| Requests · throughput | 21379 · 344/s |
| **5xx responses** | **0** |
| Non-2xx responses | 94% - these are `304 Not Modified` from the state ETag, not errors |
| p95 · max latency | 7.71 ms · 925 ms |
| Back to 2 replicas after the spike | 257s |

Two details worth reading:

* **`http_req_failed` is 94% and that is the system working.** The console polls `/api/state`; the api answers
  `304 Not Modified` whenever the engine's state hash has not changed, so the browser re-renders nothing and the
  payload is never serialised. k6 scores any non-2xx as "failed", which is why the number to look at is the
  explicit `not 5xx` check - 0 failures out of 21379 requests.
* **The autoscaler rule is measured, not guessed.** `docs/evidence/loadtest-report.md` found the per-replica
  limit (~50 concurrent consoles per 0.5-CPU replica). The HPA encodes it: 60% CPU target,
  `scaleUp.stabilizationWindowSeconds: 0` so a surge is answered on the next 15s metrics window, and
  `scaleDown` held at 120s with one pod per minute so a brief dip cannot cause flapping.


HPA timeline (sampled every 3s; only the moments the replica count changed are shown, full log in
`/tmp/fsip-k8s-demo/hpa_timeline.txt`):

```
15:31:46  ready=2 desired=2 cpu=4%
15:32:32  ready=2 desired=4 cpu=150%
15:32:43  ready=4 desired=4 cpu=150%
15:32:46  ready=4 desired=5 cpu=141%
15:32:57  ready=5 desired=5 cpu=141%
15:35:06  ready=4 desired=4 cpu=2%
15:36:05  ready=3 desired=3 cpu=3%
```

## What Kubernetes is deliberately *not* allowed to do

| Decision | Reason |
|---|---|
| `engine`: `replicas: 1`, `strategy: Recreate` | The engine is the only writer to the simulator. Two engines could dispatch the same fuel twice, so Kubernetes must never run a second one - not even during the overlap a RollingUpdate would create. |
| No HPA on the engine | Its throughput is bounded by the simulator tick, not by our CPU. Scaling it out would buy nothing and risk double dispatch. |
| Break-glass served by a sidecar inside the engine pod | The printable fallback plan must survive the engine *process* dying; a second container in the same pod keeps serving `/breakglass/latest.html` while the engine restarts. |
| `publishNotReadyAddresses: true` on the break-glass Service | Its whole purpose is to answer while the pod is *not* ready. |
| PodDisruptionBudgets on api and frontend | A node drain or cluster upgrade may never take the last pod that serves the console. |
| Redis as a cache-only StatefulSet (no persistence) | The engine rebuilds it from the simulator and the database on boot, which the F7 "Redis killed" drill proves. |

## Pods after the demo

```
NAME                                 READY   STATUS      RESTARTS   AGE     IP            NODE                 NOMINATED NODE   READINESS GATES
fsip-api-66c76dd57b-hvq9f            0/1     Completed   0          4m38s   10.244.0.35   fsip-control-plane   <none>           <none>
fsip-api-66c76dd57b-jzxb2            1/1     Running     0          39m     10.244.0.9    fsip-control-plane   <none>           <none>
fsip-api-66c76dd57b-msq84            1/1     Running     0          39m     10.244.0.15   fsip-control-plane   <none>           <none>
fsip-engine-684ff75f94-qk6tk         2/2     Running     0          31m     10.244.0.16   fsip-control-plane   <none>           <none>
fsip-frontend-6f54b48d64-kr892       1/1     Running     0          39m     10.244.0.7    fsip-control-plane   <none>           <none>
fsip-frontend-6f54b48d64-m8cxg       1/1     Running     0          39m     10.244.0.10   fsip-control-plane   <none>           <none>
fsip-intelligence-7d7d87d7df-mjgh7   1/1     Running     0          5m39s   10.244.0.33   fsip-control-plane   <none>           <none>
fsip-postgres-0                      1/1     Running     0          39m     10.244.0.14   fsip-control-plane   <none>           <none>
fsip-redis-0                         1/1     Running     0          39m     10.244.0.12   fsip-control-plane   <none>           <none>
fsip-simulator-689db8d8f9-pdnbl      1/1     Running     0          39m     10.244.0.6    fsip-control-plane   <none>           <none>
```
