"""Screenshot the provisioned Grafana dashboards (kiosk mode, anonymous viewer) for docs/evidence.

    .venv/bin/python scripts/demo/grafana_shots.py --prefix b7-normal [--from now-15m]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

GRAFANA = os.getenv("GRAFANA_URL", "http://localhost:13001")
OUT = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "screenshots"
BOARDS = {"fsip-ops": "ops", "fsip-health": "service-health", "fsip-intel": "intelligence", "fsip-logs": "logs"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="grafana")
    ap.add_argument("--from", dest="frm", default="now-15m")
    ap.add_argument("--only", nargs="*", default=list(BOARDS))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # explicit locale: headless Chromium in WSL has none and Grafana crashes on boot ("Incorrect locale information")
        page = browser.new_page(viewport={"width": 1800, "height": 1100}, locale="en-US", timezone_id="Asia/Dhaka")
        for uid in args.only:
            page.goto(f"{GRAFANA}/d/{uid}?orgId=1&kiosk&from={args.frm}&to=now&refresh=")
            page.wait_for_timeout(6000)  # panels query Prometheus/Loki asynchronously
            name = f"{args.prefix}-{BOARDS[uid]}.png"
            page.screenshot(path=str(OUT / name), full_page=True)
            print("saved", name)
        browser.close()


if __name__ == "__main__":
    main()
