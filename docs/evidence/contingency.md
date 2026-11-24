# N-1 contingency: what one failure would cost, before it happens

Generated 2026-11-24 16:59 UTC against a running stack
(`.venv/bin/python scripts/demo/n1_evidence.py --inject`).

Operators of any supply network ask the same question before anything breaks: **if I lose one asset, what breaks,
and do I have time to react?** That is the N-1 criterion from power-grid planning, and answering it needs a
forward simulation we control — the simulator cannot be forked, so we cannot ask it "what would tomorrow look
like if this route were cut".

So the engine replays the network forward every 16 ticks with each asset removed in turn, **closed-loop**: our own
replenishment policy reacts every simulated tick, exactly as it would in production. The scan covers every route,
depot and station at two outage lengths and finishes in well under a second, which is why it can run inside a tick
without touching the simulator.

Two different questions share the report, and conflating them would mislead:

| Question | Answer it produces | Depends on |
|---|---|---|
| Does anything run dry, and can fuel arrive another way in time? | `critical` | today's inventory |
| Which station has only one way in at all? | `sole_supply_path` | the shape of the network |

The second is the one that matters on a good day. A network with full tanks absorbs every single failure — and
still has stations that nothing can reach if one route is cut.

### A calm network

Every tank is comfortable. This is what the scan says when nothing is wrong, and it is still not empty of information.

Scan at tick 273, 96 ticks ahead, computed in **390 ms**
(forecast: seasonal@1.0.0).
Baseline without any failure: 5,040 L unmet,
service level 94.6%.

Single supply paths (no other route exists): **Gazipur Depot → Tongi Industrial Station**, **Patiya Depot → Cox's Bazar Regional Station**

Only working route today (alternatives disrupted right now): none

| Asset lost | kind | 24 ticks | 48 ticks | Runs dry in | Reroute |
|---|---|---|---|---|---|
| Gazipur Depot → Mirpur Fuel Station | route | **2,308 L** | **2,308 L** | - | 1.0 h |
| Gazipur Depot → Tongi Industrial Station | route | **1,568 L** | **5,320 L** | - | **no other route** |
| Patiya Depot → Karnaphuli Highway Station | route | absorbed | absorbed | - | 1.2 h |
| Patiya Depot → Cox's Bazar Regional Station | route | **1,413 L** | **4,694 L** | - | **no other route** |
| Gazipur Depot → Karnaphuli Highway Station | route | absorbed | absorbed | - | 1.0 h |
| Patiya Depot → Mirpur Fuel Station | route | absorbed | absorbed | - | 1.0 h |
| Gazipur Depot | depot | **3,876 L** | **8,395 L** | - | 1.0 h |
| Patiya Depot | depot | absorbed | **3,803 L** | - | - |
| Mirpur Fuel Station | station | absorbed | absorbed | - | n/a |
| Tongi Industrial Station | station | absorbed | **317 L** | - | n/a |
| Karnaphuli Highway Station | station | absorbed | absorbed | - | n/a |
| Cox's Bazar Regional Station | station | absorbed | absorbed | - | n/a |

* Tongi Industrial Station has no second route at all. Pre-stock it before Gazipur Depot → Tongi Industrial Station is ever cut: nothing can reach it while that route is down.
* Cox's Bazar Regional Station has no second route at all. Pre-stock it before Patiya Depot → Cox's Bazar Regional Station is ever cut: nothing can reach it while that route is down.

### The same network, strained

A supply shortfall at the depots and a cut to a single-route station, injected through the demo controls.

Scan at tick 289, 96 ticks ahead, computed in **194 ms**
(forecast: seasonal@1.0.0).
Baseline without any failure: 21,361 L unmet,
service level 77.1%.

Single supply paths (no other route exists): **Gazipur Depot → Tongi Industrial Station**, **Patiya Depot → Cox's Bazar Regional Station**

Only working route today (alternatives disrupted right now): none

| Asset lost | kind | 24 ticks | 48 ticks | Runs dry in | Reroute |
|---|---|---|---|---|---|
| Gazipur Depot → Mirpur Fuel Station | route | **2,593 L** | **2,814 L** | - | - |
| Gazipur Depot → Tongi Industrial Station | route | absorbed | absorbed | - | **no other route** |
| Patiya Depot → Karnaphuli Highway Station | route | absorbed | absorbed | - | - |
| Patiya Depot → Cox's Bazar Regional Station | route | **2,640 L** | **7,783 L** | - | **no other route** |
| Gazipur Depot → Karnaphuli Highway Station | route | absorbed | absorbed | - | - |
| Patiya Depot → Mirpur Fuel Station | route | absorbed | absorbed | - | - |
| Gazipur Depot | depot | **2,593 L** | **3,940 L** | - | - |
| Patiya Depot | depot | **690 L** | **6,943 L** | - | - |
| Mirpur Fuel Station | station | **16 L** | absorbed | - | n/a |
| Tongi Industrial Station | station | absorbed | absorbed | - | n/a |
| Karnaphuli Highway Station | station | absorbed | absorbed | - | n/a |
| Cox's Bazar Regional Station | station | absorbed | absorbed | - | n/a |

* Cox's Bazar Regional Station has no second route at all. Pre-stock it before Patiya Depot → Cox's Bazar Regional Station is ever cut: nothing can reach it while that route is down.
* Tongi Industrial Station has no second route at all. Pre-stock it before Gazipur Depot → Tongi Industrial Station is ever cut: nothing can reach it while that route is down.

## What the numbers mean

* **Extra litres** are measured against a baseline projection with nothing removed, so a network already under
  strain does not make every asset look catastrophic. Demand a *closed station* cannot sell is reported separately
  as `unavoidable_unmet` and never used to rank fragility — otherwise every station outage would outrank every
  real weakness, and an operator would be sent to fix something that cannot be fixed.
* **Runs dry in** (`tts_hours`) is the time to the first stockout if the asset goes now. **Reroute** (`ttr_hours`)
  is how fast fuel could reach the worst-hit station another way, including one tick to plan. `critical` is simply
  `tts < ttr`: the network empties before help can arrive.
* The projection uses the **p50 forecast**, so these are estimates. It also runs the simple replenishment rule
  rather than the LP, because 25 projections must fit inside a tick — the LP does better in reality, so a
  `critical` verdict here is conservative.

## Where it is in the console

The **Risk** page leads with this panel: single supply paths called out first, then the heatmap, then the
sentences an operator can act on today. Screenshot: `screenshots/e3-n1-contingency.png`.

Metrics: `fsip_n1_critical_assets`, `fsip_n1_worst_extra_unmet_liters`, `fsip_n1_scan_seconds`.
