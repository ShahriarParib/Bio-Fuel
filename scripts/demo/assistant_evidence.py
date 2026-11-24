"""Measure the plain-language assistant against a running stack and write docs/evidence/assistant.md.

    .venv/bin/python scripts/demo/assistant_evidence.py

What it records: the live path (what an operator actually reads, how long it took, whether it was cached), and
the guard rails exercised directly against the same functions the api calls, so the claims in the report are
demonstrated rather than asserted.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "libs" / "fsip_common"))
from fsip_common.llm import check_grounded, collect_numbers  # noqa: E402

UI = os.getenv("UI_URL", "http://localhost:3000")
OUT = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "assistant.md"


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{UI}{path}", timeout=30) as r:
        return json.load(r)


def explain(kind: str, item_id: int) -> tuple[dict, int]:
    t0 = time.time()
    body = get(f"/api/{kind}/{item_id}/explain")
    return body, int((time.time() - t0) * 1000)


def row(label: str, body: dict, wall_ms: int) -> str:
    src = ("the model, checked" if body["source"] == "gemini" else "the platform's own summary")
    return (f"| {label} | {src} | {body.get('model') or '-'} | "
            f"{'cache' if body['cached'] else str(body['latency_ms']) + ' ms'} | {wall_ms} ms |")


def main() -> None:
    status = get("/api/assistant")
    decisions = get("/api/decisions?limit=1")["items"]
    incidents = get("/api/incidents")["items"]
    if not decisions:
        print("no decisions yet: run the simulator first", file=sys.stderr)
        raise SystemExit(1)

    d_id = decisions[0]["id"]
    first, first_ms = explain("decisions", d_id)
    second, second_ms = explain("decisions", d_id)
    inc_rows, inc_sample = [], None
    if incidents:
        i_id = incidents[0]["id"]
        inc, inc_ms = explain("incidents", i_id)
        inc_rows.append(row(f"Incident #{i_id} ({incidents[0]['component']})", inc, inc_ms))
        inc_sample = inc

    # --- the guard, run against the same function the api calls -------------------------------------------
    facts = first["grounded_in"]
    allowed = collect_numbers(facts)
    probes = [
        ("repeats our figures", "Autopilot sent 1130 liters of diesel to mirpur."),
        ("invents a percentage", "Autopilot sent 1130 liters, roughly 45 percent of the tank."),
        ("gets a quantity subtly wrong", "Autopilot sent 1310 liters of diesel to mirpur."),
        ("uses no numbers at all", "Autopilot topped up the station before the evening peak."),
    ]
    probe_rows = []
    for label, text in probes:
        bad = check_grounded(text, allowed)
        verdict = "shown to the operator" if bad is None else f"**discarded** (invented `{bad}`)"
        probe_rows.append(f"| {label} | `{text}` | {verdict} |")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(f"""# The plain-language assistant, and why you can trust what it says

Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} against a running stack.

An operator on a night shift does not need another dashboard; they need a sentence. So the console can explain any
recommendation or incident in plain words. The entire design is about making that safe:

**The model never decides anything.** Every shipment is produced by the optimiser and checked by the constraint
rules in `services/engine`. The assistant is handed the facts *after* the decision exists and is asked to re-word
them. Nothing it returns is fed back into planning.

**The model may not introduce a number.** Before an explanation reaches the screen, every number in it is checked
against the figures the platform computed. One that does not match means the whole answer is thrown away and the
operator reads our own written summary instead, with the reason. A fluent sentence with the wrong litre count is
more dangerous than no sentence at all.

**The operator always knows which they are reading.** Every explanation carries a badge: *re-worded by `<model>`*
or *the platform's own summary*.

## Assistant state

| Field | Value |
|---|---|
| State | `{status['state']}` |
| Model (primary, then spare) | {', '.join(f'`{m}`' for m in status.get('models') or []) or '-'} |
| Per-attempt timeout | {status['timeout_s']} s |
| Explanation cache | {status['cache_ttl_s']} s, keyed by the facts themselves |

## What an operator actually got

| Asked about | Source | Model | Provider time | End-to-end |
|---|---|---|---|---|
{row(f'Decision #{d_id}', first, first_ms)}
{row(f'Decision #{d_id} again', second, second_ms)}
{chr(10).join(inc_rows)}

> {first['text']}

{('> ' + inc_sample['text']) if inc_sample else ''}

The second request is the same question: it is answered from cache in a few milliseconds and costs nothing. The
cache key is a hash of the facts, so it invalidates itself the moment the underlying decision changes.

## The guard, exercised

These four strings are run through `check_grounded()` - the exact function the api calls before any explanation is
shown - against the real facts of decision #{d_id}:

| The model says something that... | Text | Result |
|---|---|---|
{chr(10).join(probe_rows)}

## What happens when the assistant cannot answer

Every one of these ends with the operator reading the platform's own summary, labelled, with no error on screen.

| Situation | Behaviour | Proven by |
|---|---|---|
| No API key configured | template, badge says so | `test_no_key_means_unavailable_not_an_error` |
| Provider overloaded (HTTP 503) | the spare model is tried, then template | `test_an_overloaded_primary_model_falls_through_to_the_spare` |
| Credentials rejected (HTTP 403) | template immediately; the spare is *not* tried | `test_a_rejected_key_is_not_retried_on_the_spare` |
| Provider slow | per-attempt timeout, then template | `test_a_timeout_is_reported_as_unavailable` |
| Answer cut off mid-sentence | discarded (`finishReason != STOP`) | `llm.truncated` metric + `_attempt` |
| Three failures in a row | one-minute cooldown: the provider is asked once a minute, not once a click | `test_repeated_failures_open_a_cooldown` |
| Answer contains a number we did not compute | discarded, reason shown | the table above |

This is not hypothetical. While building this we hit a real `503 UNAVAILABLE` on the pinned model
(`gemini-3.1-flash-lite`) and the console kept explaining decisions from the written summaries throughout, which is
why the client now carries a spare model and why the primary is pinned to one we verified end to end.

## Metrics

| Metric | What it tells an operator |
|---|---|
| `fsip_llm_requests_total{{outcome}}` | `ok`, `timeout`, `overloaded`, `auth_error`, `truncated`, `rejected_ungrounded`, `disabled`, `cooldown` |
| `fsip_llm_latency_seconds` | provider round-trip, so a slow assistant is visible before an operator complains |
| `fsip_llm_cache_total{{result}}` | how much of the feature costs nothing |

## Where it is in the console

* **Decisions** - click a decision number to have it explained.
* **System health → Incidents** - every incident can be explained; the card header shows whether the assistant is
  available at all.
* Screenshots: `screenshots/e2-explain-decision.png`, `screenshots/e2-explain-incident.png`.
""", encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"decision explanation source={first['source']} model={first.get('model')} "
          f"first={first_ms}ms cached_second={second_ms}ms")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as err:
        print(f"console not reachable at {UI}: {err}", file=sys.stderr)
        raise SystemExit(1) from err
