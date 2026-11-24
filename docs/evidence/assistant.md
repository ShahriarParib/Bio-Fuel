# The plain-language assistant, and why you can trust what it says

Generated 2026-11-24 16:38 UTC against a running stack.

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
| Per-attempt timeout | 6.0 s |
| Explanation cache | 900 s, keyed by the facts themselves |

## What an operator actually got

| Asked about | Source | Model | Provider time | End-to-end |
|---|---|---|---|---|
| Decision #8029 | the model, checked | gemini-3.5-flash-lite | 2356 ms | 3470 ms |
| Decision #8029 again | the model, checked | gemini-3.5-flash-lite | cache | 5 ms |
| Incident #9 (redis) | the platform's own summary | - | 0 ms | 12434 ms |

> We are executing shipments totaling 2780 liters of diesel via autopilot based on seasonal forecasting. Gazipur is sending 1130 liters to Mirpur Fuel Station because it is at 52 percent capacity, while Patiya is sending 1650 liters to Karnaphuli Highway Station to maintain cover. Please watch station inventory levels closely as operations continue.

> The redis component reported: unreachable. The platform kept working by this route: api reads state directly from the engine; live updates by polling. It recovered on its own after 15.9 seconds, with no operator action.

The second request is the same question, answered from cache in a few milliseconds at no cost. The cache key is a hash of the facts, so it invalidates itself the moment the underlying decision changes.

## Two things written for a human to act on

An explanation answers "why this?". These two answer "what do I do now?", and they are the places where a wrong
number would travel furthest — a handover note gets pasted into a chat, a postmortem gets read the next morning by
people who were not there. Both go through the same guard.

**Shift handover** (`GET /api/summary?window_ticks=96`, template, 12434 ms) — the note an outgoing
operator leaves behind:

> Over the last 96 ticks (t96 to 192) the network served 100% of demand: 186,352 L delivered, 0 L unmet. The platform made 30 decisions this shift (90,780 L dispatched, 0 failed shipments, 0 L lost to overflow), running in advisory mode on the optimizer planner. 9 component incident(s) opened and closed with nobody acting, the first being redis (unreachable) back in 15.9 s. No station is at elevated risk right now.

**Postmortem** for incident #9 (`GET /api/incidents/9/postmortem`, template, 13644 ms):

> The redis component failed with: unreachable. The platform carried on by this route: api reads state directly from the engine; live updates by polling. Operating mode moved: engine mode → NORMAL. It recovered 15.9 s after detection with no operator action, so no shipment waited on a human. The platform records the symptom, not the root cause, so the cause is whatever the component reported.

Counts in the handover are given to the model explicitly rather than left to be counted off a list: the lists it
sees are truncated to four examples, so a model counting items would state a number that is true of the list and
false of the shift. In the postmortem, the facts separate *actions the platform took* from *operating-mode
transitions observed*, because an early draft described a mode change as if it were the fix.

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
* **System health → Incidents** - every incident can be explained, and a resolved one can be written up as a
  postmortem; the card header shows whether the assistant is available at all.
* **Overview** - the shift-handover card, with a copy button for pasting into a chat or a logbook.
* Screenshots: `screenshots/e2-explain-decision.png`, `screenshots/e2-explain-incident.png`,
  `screenshots/e2-shift-handover.png`, `screenshots/e2-postmortem.png`.
