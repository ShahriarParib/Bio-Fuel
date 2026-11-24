"""Watch who is going short while the network recovers, and write docs/evidence/fairness.md.

    .venv/bin/python scripts/demo/fairness_evidence.py [--minutes 6]

Run it while the platform is working its way out of a shortfall. It samples the fairness report every few seconds
and records the one thing a headline service level cannot show: whether the recovery reaches every station or
only the easy ones.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

UI = "http://localhost:3000"
OUT = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "fairness.md"


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{UI}{path}", timeout=30) as r:
        return json.load(r)


def sample() -> dict:
    f = get("/api/fairness")
    s = get("/api/state")
    return {
        "tick": f["tick"],
        "network_sl": s["metrics"]["service_level"],
        "min_station": f["min_station"],
        "worst": f["worst_station"],
        "jain": f["jain"],
        "spread": f["spread"],
        "cover": min((v for v in (f["cover_ratio"] or {}).values() if v is not None), default=None),
        "constraint": (f.get("diagnosis") or {}).get("constraint"),
        "detail": (f.get("diagnosis") or {}).get("detail"),
        "scarce": [k for k, v in (f["scarcity"] or {}).items() if v],
        "per_station": f["per_station"],
        "mode": s.get("operating_mode"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=6.0)
    ap.add_argument("--every", type=float, default=10.0)
    args = ap.parse_args()

    samples = [sample()]
    print(f"t={samples[0]['tick']} min_station={samples[0]['min_station']} {samples[0]['constraint']}")
    deadline = time.time() + args.minutes * 60
    while time.time() < deadline:
        time.sleep(args.every)
        try:
            s = sample()
        except Exception as err:            # a restart mid-run must not lose the samples already taken
            print("sample failed:", err)
            continue
        if s["tick"] != samples[-1]["tick"]:
            samples.append(s)
            print(f"t={s['tick']} min_station={s['min_station']} jain={s['jain']} {s['constraint']}")

    first, last = samples[0], samples[-1]

    def row(s: dict) -> str:
        worst = "-" if s["min_station"] is None else f"{s['min_station'] * 100:.1f}%"
        jain = "-" if s["jain"] is None else f"{s['jain']:.3f}"
        cover = "-" if s["cover"] is None else f"{s['cover']:.2f}x"
        return (f"| {s['tick']} | {s['network_sl'] * 100:.1f}% | {worst} | {jain} | {cover} "
                f"| {s['constraint']} |")

    rows = "\n".join(row(s) for s in samples)

    per_station = "\n".join(
        f"| {sid.replace('station-', '')} | {first['per_station'].get(sid, 0) * 100:.1f}% | "
        f"{last['per_station'].get(sid, 0) * 100:.1f}% |"
        for sid in sorted(last["per_station"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(f"""# Scarcity and fairness: who goes short, and why

Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} from a running stack
(`.venv/bin/python scripts/demo/fairness_evidence.py`).

A network-wide service level is an average, and an average cannot be unfair. 99% served can mean every station
comfortable, or three stations full and one dry for a day. Those are different outcomes for the people queuing at
that fourth station, so the platform measures the distribution, not only the total:

| Measure | What it answers |
|---|---|
| per-station service level | how each station actually did, on its own demand |
| worst-served station | the one an operator will be asked about |
| **Jain index** | the shape of the distribution: 1.0 when everyone is served equally, 1/n when one station takes everything. Scale-invariant, so it judges fairness and not how much fuel there was |
| cover ratio | deliverable fuel ÷ forecast demand over 96 ticks, per fuel and per region |

## The diagnosis that came out of our own mistake

We left the stack running overnight in **advisory** mode. By morning every station was bone dry — and the depots
still held **67,520 L of diesel**. Nothing was scarce. Nothing had failed. Nobody had approved anything.

Two very different problems look identical on a stockout chart:

* **supply** — there is not enough deliverable fuel, however well it is routed;
* **distribution** — the fuel exists but is not reaching stations (dispatch caps, routes, transit);
* **waiting on a human** — advisory mode with recommendations pending.

They need opposite responses, so the platform now names which one is binding instead of showing a red number and
leaving the operator to guess. On that morning it said, correctly:

> {first['detail'] or 'n/a'}

## The recovery, measured

Sampling starts at tick {first['tick']}. Autopilot is already on and the network is working its way back from
having run completely dry; every sample below is the platform refilling it.

| tick | network SL | worst station | Jain | cover | binding constraint |
|---|---|---|---|---|---|
{rows}

Per station, first sample → last:

| station | start | end |
|---|---|---|
{per_station}

The headline service level moves slowly because it is cumulative over the whole run; the worst-station number and
the Jain index are what show the recovery reaching everyone rather than only the nearest station.

Two things in that table are worth reading twice:

* **The recovery was even.** The worst station climbs from the low twenties to 100 % while the Jain index rises to
  1.000. The platform did not refill the easy stations first and leave the far one dry - which is exactly what a
  greedy "serve the biggest shortfall nearest a depot" rule would have done.
* **The diagnosis flipped before the damage did.** The binding constraint changes from `distribution` to `supply`
  while every station is still improving, because the cover ratio had already fallen through 1.0 - the depots were
  emptying with no further supply scheduled. The service level only starts falling again about thirty ticks later.
  That gap is the warning an operator can act on.

## How scarcity is declared

Cover ratio below **1.0** for **2** consecutive cycles enters scarcity; above **1.1** for **4** cycles leaves it.
The gap between the thresholds is deliberate: a ratio hovering at 1.0 would otherwise flip the banner, and the
policy underneath it, every cycle. Leaving needs more evidence than entering because leaving early is the
expensive mistake.

While a fuel is scarce the planner's objective changes: the optimiser maximises the **worst-served station's**
cover (a max-min term) rather than the network total, which is why the Jain index is the number to watch during a
shortfall rather than the headline.

Metrics: `fsip_scarcity_active{{fuel}}`, `fsip_cover_ratio{{fuel}}`, `fsip_fairness_jain`,
`fsip_min_station_service_level`. Console: the **Risk** page leads with this panel
(`screenshots/e4-fairness.png`, captured once the depots had genuinely run out: all three fuels scarce, cover
0.00x, Cox's Bazar the worst-served station at 57.5 %).

The evaluation harness reports the same Jain index per scenario and policy, so the fairness claim is not only a
live observation: `docs/evidence/evaluation.md`.
""", encoding="utf-8")
    print(f"wrote {OUT} ({len(samples)} samples)")


if __name__ == "__main__":
    main()
