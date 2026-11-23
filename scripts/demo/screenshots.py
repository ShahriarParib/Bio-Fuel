"""Capture console screenshots for docs/evidence and smoke-test the approve flow through the real UI.

    .venv/bin/python scripts/demo/screenshots.py [--approve] [--prefix b4]
Requires: pip install playwright==1.49.1 && playwright install chromium (dev only, not in the images).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

UI = os.getenv("UI_URL", "http://localhost:3000")
TOKEN = os.getenv("API_OPERATOR_TOKEN", "change-me-operator")
OUT = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "screenshots"
PAGES = ["overview", "recommendations", "risk", "alerts", "decisions", "health"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve", action="store_true", help="click Approve on the first recommendation")
    ap.add_argument("--prefix", default="console")
    ap.add_argument("--pages", nargs="*", default=PAGES)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        page.goto(f"{UI}/overview")
        page.evaluate(f"localStorage.setItem('fsip.operatorToken', '{TOKEN}')")
        for name in args.pages:
            page.goto(f"{UI}/{name}")
            page.wait_for_timeout(2500)
            page.screenshot(path=str(OUT / f"{args.prefix}-{name}.png"), full_page=True)
            print("saved", f"{args.prefix}-{name}.png")
        if args.approve:
            page.goto(f"{UI}/recommendations")
            page.wait_for_timeout(2000)
            cards = page.locator('[data-testid^="rec-"]')
            if cards.count() == 0:
                print("no recommendation to approve")
            else:
                first = cards.first
                rec_id = first.get_attribute("data-testid")
                first.locator('[data-testid="why-toggle"]').click()
                page.wait_for_timeout(300)
                page.screenshot(path=str(OUT / f"{args.prefix}-recommendation-why.png"), full_page=True)
                first.locator('[data-testid="approve-btn"]').click()
                page.wait_for_timeout(2500)
                print("approved", rec_id)
                page.goto(f"{UI}/decisions")
                page.wait_for_timeout(2500)
                page.screenshot(path=str(OUT / f"{args.prefix}-decisions-after-approve.png"), full_page=True)
        browser.close()


if __name__ == "__main__":
    main()
