"""Break-glass plan (06 F-BREAKGLASS): a static HTML + CSV the operator can open when api/engine/UI are down.

Written by the engine every few ticks into a volume that nginx serves at /breakglass/latest.html. Plain HTML, no
JavaScript, no dependency on any of our services: if everything else is broken, this page still opens.
"""
from __future__ import annotations

import csv
import html
import io
import os
import tempfile
from datetime import UTC, datetime
from typing import Any


def render(state: dict[str, Any]) -> tuple[str, str]:
    """(html, csv) from the public state JSON. Pure: unit-tested without files."""
    esc = html.escape
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    stations = state.get("stations", [])
    names = {s["id"]: s.get("name", s["id"]) for s in stations}
    names.update({d["id"]: d.get("name", d["id"]) for d in state.get("depots", [])})
    cover = {(c["station_id"], c["fuel_type"]): c.get("hours_of_cover")
             for c in ((state.get("cover") or {}).get("stations") or [])}

    plan_rows: list[list[str]] = []
    for d in state.get("pending_recommendations", []):
        for ln in d.get("lines", []):
            plan_rows.append(["RECOMMENDED", str(d.get("id")), names.get(ln.get("source_depot_id"), ""),
                              names.get(ln.get("destination_station_id"), ""), ln.get("fuel_type", ""),
                              f"{float(ln.get('quantity', 0)):.0f}", ln.get("route_id", "")])
    for a in state.get("allocations_active", []):
        plan_rows.append(["IN TRANSIT", str(a.get("id")), names.get(a.get("source_depot_id"), ""),
                          names.get(a.get("destination_station_id"), ""), a.get("fuel_type", ""),
                          f"{float(a.get('quantity', 0)):.0f}", a.get("route_id", "")])

    stock_rows = []
    for s in stations:
        for f, inv in (s.get("inventory") or {}).items():
            cap = (s.get("capacity") or {}).get(f) or 0
            h = cover.get((s["id"], f))
            stock_rows.append([s.get("name", s["id"]), f, f"{float(inv):.0f}", f"{float(cap):.0f}",
                               "" if h is None else f"{h:.1f}"])
    stock_rows.sort(key=lambda r: float(r[4]) if r[4] else 1e9)

    down_rows = [[r["id"], names.get(r.get("source_depot_id"), ""), names.get(r.get("destination_station_id"), "")]
                 for r in state.get("routes", []) if r.get("status") != "AVAILABLE"]

    def table(head: list[str], rows: list[list[str]]) -> str:
        th = "".join(f"<th>{esc(h)}</th>" for h in head)
        body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
        return f"<table><thead><tr>{th}</tr></thead><tbody>{body or '<tr><td colspan=9>none</td></tr>'}</tbody></table>"

    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>FSIP break-glass plan</title>
<meta http-equiv="refresh" content="30">
<style>body{{font:14px system-ui,sans-serif;margin:16px;background:#fff;color:#111}}
table{{border-collapse:collapse;margin:8px 0 20px}}td,th{{border:1px solid #bbb;padding:3px 8px;text-align:left}}
th{{background:#eee}}.warn{{background:#fde68a;padding:6px 10px;font-weight:600}}</style></head><body>
<div class="warn">SIMULATED ENVIRONMENT. BREAK-GLASS PLAN: static copy for when the console is unavailable.</div>
<h1>Fuel dispatch plan</h1>
<p>Data from simulator tick <b>{esc(str(state.get('tick')))}</b> ({esc(str(state.get('clock_label', '')))}),
run #{esc(str(state.get('sim_run_id')))}. Written {generated}.
Engine mode: <b>{esc(str(state.get('engine_mode')))}</b>.</p>
<p>Use: call the depots below and dispatch the RECOMMENDED lines by hand, lowest hours-of-cover first.
Do not dispatch on a route that is disrupted.</p>
<h2>Dispatch plan</h2>
{table(["Status", "Ref", "From depot", "To station", "Fuel", "Litres", "Route"], plan_rows)}
<h2>Stock and hours of cover (lowest first)</h2>
{table(["Station", "Fuel", "Litres", "Capacity", "Hours of cover"], stock_rows)}
<h2>Routes not available</h2>
{table(["Route", "From", "To"], down_rows)}
</body></html>"""

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["status", "ref", "from_depot", "to_station", "fuel", "liters", "route"])
    w.writerows(plan_rows)
    return page, buf.getvalue()


def write(directory: str, state: dict[str, Any]) -> None:
    """Atomic write (temp file + rename) so nginx never serves half a page."""
    os.makedirs(directory, exist_ok=True)
    page, csv_text = render(state)
    for name, content in (("latest.html", page), ("latest.csv", csv_text)):
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(directory, name))
