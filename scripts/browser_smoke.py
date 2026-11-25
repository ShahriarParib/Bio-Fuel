"""Drive the real console in a real browser and assert what an operator must be able to see and do.

    .venv/bin/python scripts/browser_smoke.py [--shots docs/evidence/screenshots]

The unit tests mock `fetch`, so they prove the components behave given a payload. They cannot prove the console
talks to the api correctly, that nginx proxies `/api`, that the SPA routes resolve, or that clicking Approve
reaches the simulator. This does, and it runs in CI against the official simulator image.

Exit code 0 only if every check passed; every failure is printed with what was expected.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

UI = os.getenv("UI_URL", "http://localhost:3000")
SIM = os.getenv("SIM_URL", "http://localhost:8000")
TOKEN = os.getenv("API_OPERATOR_TOKEN", "change-me-operator")

failures: list[str] = []
checks = 0


def check(ok: bool, what: str, detail: str = "") -> bool:
    global checks
    checks += 1
    if ok:
        print(f"  ok    {what}")
    else:
        print(f"  FAIL  {what}{(' - ' + detail) if detail else ''}")
        failures.append(what)
    return ok


def api(path: str) -> dict:
    with urllib.request.urlopen(f"{UI}{path}", timeout=20) as r:
        return json.load(r)


def run_simulator_until(tick: int, page: Page) -> int:
    """The simulator ships paused; a judge presses Run. So does this test."""
    try:
        urllib.request.urlopen(urllib.request.Request(f"{SIM}/admin/run", method="POST"), timeout=20).read()
    except Exception as err:
        print(f"  (could not start the simulator: {err})")
    for _ in range(60):
        try:
            if api("/api/state")["tick"] >= tick:
                break
        except Exception:
            pass
        page.wait_for_timeout(2000)
    return api("/api/state")["tick"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="", help="directory to write screenshots into")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1600, "height": 1000},
                                locale="en-US", timezone_id="Asia/Dhaka")
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        print("\n== the console loads and talks to the api")
        page.goto(f"{UI}/overview", wait_until="networkidle")
        check(page.locator('[data-testid="overview"]').count() > 0, "the Overview page renders")
        check(page.locator('[data-testid="kpi-sl"]').count() > 0, "the service-level KPI is on screen")

        print("\n== the simulated world runs")
        tick = run_simulator_until(8, page)
        check(tick >= 8, "the simulator advances past tick 8", f"stopped at {tick}")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(2500)

        print("\n== every route resolves (the SPA is served, not 404s)")
        for route, marker in [("/overview", '[data-testid="overview"]'),
                              ("/recommendations", "main"),
                              ("/risk", '[data-testid="risk-heatmap"]'),
                              ("/decisions", "main"),
                              ("/alerts", "main"),
                              ("/health", '[data-testid="incidents"]'),
                              ("/control", "main")]:
            page.goto(f"{UI}{route}", wait_until="networkidle")
            page.wait_for_timeout(1200)
            check(page.locator(marker).count() > 0, f"{route} renders")

        print("\n== the analytics panels reach their endpoints")
        page.goto(f"{UI}/risk", wait_until="networkidle")
        page.wait_for_timeout(2500)
        check(page.locator('[data-testid="fairness"]').count() > 0, "the fairness panel is served")
        has_n1 = page.locator('[data-testid="n1-heatmap"]').count() > 0
        print(f"  info  N-1 scan published: {has_n1} (it runs every 16 ticks)")
        if shots:
            page.screenshot(path=str(shots / "ci-risk.png"), full_page=True)

        print("\n== signing in unlocks the operator controls")
        page.goto(f"{UI}/overview")
        page.evaluate(f"localStorage.setItem('fsip.operatorToken', '{TOKEN}')")
        page.goto(f"{UI}/recommendations", wait_until="networkidle")
        page.wait_for_timeout(2000)
        cards = page.locator('[data-testid^="rec-"]')
        print(f"  info  {cards.count()} recommendation card(s) on screen")

        if cards.count() > 0:
            first = cards.first
            rec_id = (first.get_attribute("data-testid") or "").removeprefix("rec-")
            check(first.locator('[data-testid="why-toggle"]').count() > 0,
                  "a recommendation explains itself (Why / constraints / alternatives)")
            check(first.locator('[data-testid="alert-card"]').count() > 0,
                  "it shows the brief's ALERT fields")
            check(first.locator('[data-testid="risk-before-after"]').count() > 0,
                  "it shows risk before → after")
            before = len(api("/api/decisions?status=EXECUTED,PARTIAL&limit=200")["items"])
            approve = first.locator('[data-testid="approve-btn"]')
            check(approve.is_enabled(), "Approve is enabled once an operator token is present")
            approve.click()
            page.wait_for_timeout(4000)
            after = len(api("/api/decisions?status=EXECUTED,PARTIAL&limit=200")["items"])
            check(after > before or api(f"/api/decisions/{rec_id}")["status"] != "PENDING_APPROVAL",
                  "clicking Approve reached the simulator", f"executed {before} → {after}")
        else:
            print("  info  nothing pending to approve (autopilot may have executed it); skipping the click")

        print("\n== the operator can always see the system's own state")
        page.goto(f"{UI}/health", wait_until="networkidle")
        page.wait_for_timeout(2000)
        check(page.locator('[data-testid="resilience-contract"]').count() > 0,
              "the resilience contract is on screen")
        if shots:
            page.screenshot(path=str(shots / "ci-health.png"), full_page=True)

        print("\n== the break-glass plan is reachable")
        resp = page.goto(f"{UI}/breakglass/latest.html")
        check(resp is not None and resp.status in (200, 304),
              "the static break-glass plan is served", f"status {resp.status if resp else 'none'}")

        real_errors = [e for e in errors if "favicon" not in e.lower()]
        check(not real_errors, "no uncaught javascript errors", "; ".join(real_errors[:3]))
        browser.close()

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("failed: " + ", ".join(failures))
        return 1
    print("BROWSER SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
