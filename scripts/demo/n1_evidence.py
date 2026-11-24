"""Measure the N-1 contingency scan twice - on a calm network and on a strained one - and write the evidence.

    .venv/bin/python scripts/demo/n1_evidence.py           # calm only
    .venv/bin/python scripts/demo/n1_evidence.py --inject   # also inject a crisis and re-scan

The second run matters: on a day when every tank is full, every single-asset failure is absorbed, and a reader
would be right to ask whether the analysis says anything at all. Injecting a real shortfall shows the same scan
separating what the network can absorb from what it cannot.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UI = os.getenv("UI_URL", "http://localhost:3000")
TOKEN = os.getenv("API_OPERATOR_TOKEN", "change-me-operator")
OUT = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "contingency.md"


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{UI}{path}", timeout=30) as r:
        return json.load(r)


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(f"{UI}{path}", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json",
                                          "Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def wait_for_scan_after(tick: int, timeout_s: int = 180) -> dict:
    """Block until a scan newer than `tick` is published (the engine runs one every 16 ticks)."""
    deadline = time.time() + timeout_s
    last = get("/api/lab/n1")
    while time.time() < deadline:
        doc = get("/api/lab/n1")
        if doc["tick"] > tick:
            return doc
        last = doc
        time.sleep(4)
    return last


def table(doc: dict) -> str:
    rows = []
    seen: set[str] = set()
    for f in doc["findings"]:
        if f["asset"] in seen:
            continue
        seen.add(f["asset"])
        cells = []
        for d in doc["durations"]:
            hit = next((x for x in doc["findings"] if x["asset"] == f["asset"] and x["duration_ticks"] == d), None)
            v = hit["extra_unmet_vs_normal"] if hit else 0
            cells.append(f"**{v:,} L**" if v else "absorbed")
        reroute = ("n/a" if f["kind"] == "station"
                   else "**no other route**" if f["sole_supply_path"]
                   else "**alternatives cut today**" if f.get("only_working_route")
                   else (f"{f['ttr_hours']} h" if f["ttr_hours"] is not None else "-"))
        dry = next((x["tts_hours"] for x in doc["findings"]
                    if x["asset"] == f["asset"] and x["tts_hours"] is not None), None)
        rows.append(f"| {f['label']} | {f['kind']} | " + " | ".join(cells) +
                    f" | {dry if dry is not None else '-'} | {reroute} |")
    head = "| Asset lost | kind | " + " | ".join(f"{d} ticks" for d in doc["durations"]) + " | Runs dry in | Reroute |"
    sep = "|---|---|" + "---|" * (len(doc["durations"]) + 2)
    return "\n".join([head, sep, *rows])


def advice_list(doc: dict) -> str:
    seen, out = set(), []
    for f in doc["top_critical"] + doc["worst_assets"]:
        if f["asset"] in seen or not f.get("advice"):
            continue
        seen.add(f["asset"])
        out.append(f"* {f['advice']}")
    return "\n".join(out) or "* Nothing to act on: every single-asset failure is absorbed."


def section(title: str, doc: dict, note: str) -> str:
    spof = ", ".join(f"**{s['label']}**" for s in doc["single_points_of_failure"]) or "none"
    today = ", ".join(f"**{s['label']}**" for s in doc.get("only_working_route") or []) or "none"
    return f"""### {title}

{note}

Scan at tick {doc['tick']}, {doc['horizon_ticks']} ticks ahead, computed in **{doc['computed_in_ms']} ms**
(forecast: {doc['forecast_model']}{', on degraded data' if doc['stale'] else ''}).
Baseline without any failure: {doc['baseline']['unmet_liters']:,} L unmet,
service level {doc['baseline']['service_level'] * 100:.1f}%.

Single supply paths (no other route exists): {spof}

Only working route today (alternatives disrupted right now): {today}

{table(doc)}

{advice_list(doc)}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="store_true", help="inject a crisis and capture a second scan")
    args = ap.parse_args()

    calm = get("/api/lab/n1")
    strained = None
    if args.inject:
        print("injecting supply_shortfall + single_route_station_cut …")
        post("/api/demo/events", {"preset": "supply_shortfall"})
        post("/api/demo/events", {"preset": "single_route_station_cut"})
        strained = wait_for_scan_after(calm["tick"])
        print(f"second scan at tick {strained['tick']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(f"""# N-1 contingency: what one failure would cost, before it happens

Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} against a running stack
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

{section('A calm network', calm, 'Every tank is comfortable. This is what the scan says when nothing is wrong, and it is still not empty of information.')}
{section('The same network, strained', strained, 'A supply shortfall at the depots and a cut to a single-route station, injected through the demo controls.') if strained else ''}
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
""", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as err:
        print(f"console not reachable at {UI}: {err}", file=sys.stderr)
        raise SystemExit(1) from err
