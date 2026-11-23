# Runbook

For whoever operates FSIP (or demos it). Every command runs from the repo root (in WSL on the demo laptop).

## Start / stop

| Task | Command |
|---|---|
| start everything | `docker compose up -d --build && bash scripts/smoke_test.sh` |
| with observability | `docker compose --profile obs up -d` |
| stop (keep data) | `docker compose --profile obs down` |
| run the simulator | Control page → Run, or `curl -X POST localhost:8000/admin/run` |
| fresh world | Control page → Reset world (clears faults too) |

## Deploy a release

```bash
scripts/deploy.sh v1.0.0            # images from GHCR (published by CI on tag v1.0.0)
scripts/deploy.sh v1.0.0 --local    # images built here: scripts/release_local.sh v1.0.0
cat deploy/versions.env             # history: version, ok|failed, time, rollback-from
```

The health gate checks: every app container running (fails fast on a crash loop), the api reports the new version,
engine ready, system status not Down, smoke test green. If the gate fails, the script rolls back to the last good
version and exits 1. Rollback demo: `scripts/demo/make_bad_release.sh v0.9.9-bad v1.0.0 && scripts/deploy.sh
v0.9.9-bad --local`.

Manual rollback: `scripts/deploy.sh <previous version> --local`.

## When something is red

Look at **System Health** first: the mode, the reasons line, and the incident timeline say what the platform already
did about it.

| Symptom | Meaning | What to do |
|---|---|---|
| banner SAFE_HOLD | simulator unreachable ≥ 60 s, no shipments | check `docker compose ps simulator`; the engine recovers on its own when it answers |
| banner STALE DATA | the simulator flags stale data | nothing; autopilot resumes when fresh data arrives. Approvals are blocked meanwhile |
| prediction service DOWN | fallback forecaster in use | `docker compose up -d intelligence`; decisions continue at confidence 0.6 (approve by hand) |
| data_quality DEGRADED | the simulator sent an invalid payload | see the alert for the field; payloads are in the `quarantine` table |
| History DB DOWN | writes are buffering | `docker compose up -d timescaledb`; the buffer flushes by itself |
| Cache DOWN | Redis down, api reads the engine | `docker compose up -d redis` |
| NO_FEASIBLE_ROUTES | a station cannot be reached | nothing can be shipped there; the plan covers the other stations |
| SLO change freeze | the domain error budget is < 25 % | focus on refills; policy switches need `override`, chaos injects need the supervisor token |
| console does not load | api or frontend down | open http://localhost:3000/breakglass/latest.html (static plan, every 4 ticks) |
| a recommendation is stuck | confidence < 0.7 or degraded data | review the "Why" panel and approve or reject; it expires after 8 ticks |

## Verify the system

| Check | Command | Expect |
|---|---|---|
| smoke | `bash scripts/smoke_test.sh` | `SMOKE OK` |
| metrics contract | `bash scripts/check_metrics.sh` | `METRICS OK` |
| autopilot end to end | `.venv/bin/python evaluation/e2e_autopilot.py --ticks 192 --assert-sl 0.98` | `E2E PASS` |
| decision quality | `.venv/bin/python evaluation/harness.py --scenario baseline --ticks 96 --assert-sl 0.99 --assert-no-failures` | `GATE PASSED` |
| all failure drills | `make chaos` (~15 min, injects real failures: not during a demo) | `11 / 11 passed` |

## Secrets

`.env` only (never committed). Operator, supervisor and internal tokens, the Grafana admin password and an optional
`ANTHROPIC_API_KEY`. Change every `change-me-*` value before exposing the console beyond localhost.
