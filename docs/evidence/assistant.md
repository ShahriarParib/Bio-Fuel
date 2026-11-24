# The plain-language assistant, and why you can trust what it says

Generated 2026-11-24 16:06 UTC against a running stack.

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
| State | `ready` |
| Model (primary, then spare) | `gemini-3.5-flash-lite`, `gemini-flash-lite-latest` |
| Per-attempt timeout | 4.0 s |
| Explanation cache | 900 s, keyed by the facts themselves |

## What an operator actually got

| Asked about | Source | Model | Provider time | End-to-end |
|---|---|---|---|---|
| Decision #8029 | the model, checked | gemini-3.5-flash-lite | 1729 ms | 1734 ms |
| Decision #8029 again | the model, checked | gemini-3.5-flash-lite | cache | 4 ms |
| Incident #9 (redis) | the model, checked | gemini-3.5-flash-lite | 1374 ms | 1378 ms |

> Autopilot has executed the dispatch of 2780 liters of diesel to maintain depot levels and cover demand. Gazipur is sending 1130 liters to Mirpur, which is currently 52 percent full with no immediate risk of running dry. Patiya is sending 1650 liters to Karnaphuli Highway Station to maintain coverage. Watch for space constraints, as two octane shipments to Patiya Coxsbazar and Patiya Karnaphuli could not be served due to insufficient room upon arrival.

> Redis went down because it was unreachable. The platform fixed this by having the api read state directly from the engine with live updates by polling, and the engine mode is now normal. The component recovered and is healthy again.

The second request is the same question: it is answered from cache in a few milliseconds and costs nothing. The
cache key is a hash of the facts, so it invalidates itself the moment the underlying decision changes.

## The guard, exercised

These four strings are run through `check_grounded()` - the exact function the api calls before any explanation is
shown - against the real facts of decision #8029:

| The model says something that... | Text | Result |
|---|---|---|
| repeats our figures | `Autopilot sent 1130 liters of diesel to mirpur.` | shown to the operator |
| invents a percentage | `Autopilot sent 1130 liters, roughly 45 percent of the tank.` | **discarded** (invented `45`) |
| gets a quantity subtly wrong | `Autopilot sent 1310 liters of diesel to mirpur.` | **discarded** (invented `1310`) |
| uses no numbers at all | `Autopilot topped up the station before the evening peak.` | shown to the operator |

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
| `fsip_llm_requests_total{outcome}` | `ok`, `timeout`, `overloaded`, `auth_error`, `truncated`, `rejected_ungrounded`, `disabled`, `cooldown` |
| `fsip_llm_latency_seconds` | provider round-trip, so a slow assistant is visible before an operator complains |
| `fsip_llm_cache_total{result}` | how much of the feature costs nothing |

## Where it is in the console

* **Decisions** - click a decision number to have it explained.
* **System health → Incidents** - every incident can be explained; the card header shows whether the assistant is
  available at all.
* Screenshots: `screenshots/e2-explain-decision.png`, `screenshots/e2-explain-incident.png`.
