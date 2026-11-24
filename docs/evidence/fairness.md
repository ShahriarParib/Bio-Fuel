# Scarcity and fairness: who goes short, and why

Generated 2026-11-24 17:21 UTC from a running stack
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

> fuel exists in the network but is not reaching stations: look at dispatch caps, route status and transit times rather than at supply

## The recovery, measured

Sampling starts at tick 754. Autopilot is already on and the network is working its way back from
having run completely dry; every sample below is the platform refilling it.

| tick | network SL | worst station | Jain | cover | binding constraint |
|---|---|---|---|---|---|
| 754 | 41.4% | 24.0% | 0.990 | 1.53x | distribution |
| 759 | 41.7% | 26.4% | 0.985 | 1.45x | distribution |
| 764 | 41.9% | 28.6% | 0.983 | 1.40x | distribution |
| 769 | 42.2% | 30.9% | 0.982 | 1.38x | distribution |
| 774 | 42.4% | 33.3% | 0.981 | 1.36x | distribution |
| 779 | 42.7% | 35.6% | 0.980 | 1.34x | distribution |
| 784 | 42.9% | 37.9% | 0.979 | 1.27x | distribution |
| 789 | 43.1% | 40.3% | 0.978 | 1.27x | distribution |
| 794 | 43.4% | 44.9% | 0.982 | 1.20x | distribution |
| 799 | 43.9% | 53.0% | 0.987 | 1.12x | distribution |
| 803 | 44.2% | 59.4% | 0.989 | 1.09x | distribution |
| 808 | 44.7% | 67.6% | 0.991 | 1.02x | distribution |
| 813 | 45.1% | 75.6% | 0.996 | 0.94x | supply |
| 818 | 45.4% | 83.7% | 0.998 | 0.91x | supply |
| 823 | 45.7% | 91.9% | 1.000 | 0.86x | supply |
| 828 | 46.0% | 98.6% | 1.000 | 0.80x | supply |
| 833 | 46.4% | 100.0% | 1.000 | 0.76x | supply |
| 838 | 46.8% | 100.0% | 1.000 | 0.69x | supply |
| 843 | 47.2% | 98.6% | 1.000 | 0.63x | supply |
| 848 | 47.5% | 96.0% | 1.000 | 0.57x | supply |
| 853 | 47.7% | 93.6% | 0.999 | 0.51x | supply |
| 858 | 47.9% | 92.2% | 0.999 | 0.48x | supply |
| 863 | 48.0% | 90.7% | 0.998 | 0.46x | supply |
| 868 | 48.2% | 89.3% | 0.998 | 0.44x | supply |
| 873 | 48.4% | 87.8% | 0.997 | 0.42x | supply |
| 878 | 48.5% | 86.5% | 0.996 | 0.39x | supply |
| 883 | 48.7% | 85.1% | 0.996 | 0.35x | supply |
| 887 | 48.8% | 84.0% | 0.995 | 0.33x | supply |
| 892 | 49.0% | 82.6% | 0.994 | 0.29x | supply |
| 897 | 49.4% | 79.9% | 0.992 | 0.24x | supply |
| 902 | 49.7% | 77.3% | 0.989 | 0.17x | supply |
| 907 | 50.0% | 74.6% | 0.986 | 0.13x | supply |
| 912 | 50.2% | 71.9% | 0.983 | 0.09x | supply |
| 917 | 50.4% | 69.3% | 0.980 | 0.06x | supply |
| 922 | 50.6% | 66.7% | 0.976 | 0.02x | supply |
| 927 | 50.8% | 63.8% | 0.972 | 0.00x | supply |
| 932 | 50.9% | 61.1% | 0.970 | 0.00x | supply |

Per station, first sample → last:

| station | start | end |
|---|---|---|
| coxsbazar | 27.5% | 61.1% |
| karnaphuli | 29.2% | 97.2% |
| mirpur | 32.0% | 95.6% |
| tongi | 24.0% | 98.0% |

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

Metrics: `fsip_scarcity_active{fuel}`, `fsip_cover_ratio{fuel}`, `fsip_fairness_jain`,
`fsip_min_station_service_level`. Console: the **Risk** page leads with this panel
(`screenshots/e4-fairness.png`, captured once the depots had genuinely run out: all three fuels scarce, cover
0.00x, Cox's Bazar the worst-served station at 57.5 %).

The evaluation harness reports the same Jain index per scenario and policy, so the fairness claim is not only a
live observation: `docs/evidence/evaluation.md`.
