# The clean-clone test

Generated 2026-11-25 15:51 UTC by `scripts/clean_clone_test.sh`.

Everything else in `docs/evidence/` was measured in a working copy that had an existing `.env`, cached images
and populated volumes. None of that is true on the machine a judge will use, so this is the one check that
matters most: **clone the repository into an empty directory and run the two commands the README gives.**

The development stack is stopped first, on purpose. It uses the same compose project name and the same ports, so
if the clone only worked because something of ours was already running, this test would say so.

| | |
|---|---|
| Source | `https://github.com/ShahriarParib/Bio-Fuel.git` |
| Commit | `f4aa35f` |
| Files in the clone | 266 |
| Setup | `cp .env.example .env` |
| Install | `docker compose up -d --build` |
| Console answered after | **~15s** (18s including the image build) |

## Smoke test

```
SERVICE        STATUS URL
simulator      OK     http://localhost:8000/v1/health
engine         OK     http://localhost:8100/health/live
engine-ready   OK     http://localhost:8100/health/ready
intelligence   OK     http://localhost:8200/health/live
api            OK     http://localhost:18080/health/live
frontend       OK     http://localhost:3000/
frontend-api   OK     http://localhost:3000/api/ping
system-status  OK     http://localhost:3000/api/system/status
overall system status: Healthy
SMOKE OK
```

## First look

```
overall: Healthy | engine mode: NORMAL | tick: 0
  fuel_simulator     Healthy
  live_stream        Healthy
  prediction_service Healthy
  database           Healthy
  redis              Healthy
  optimizer          Healthy
  data_quality       Healthy
  decision_engine    Healthy
  backend_api        Healthy
stations: 4 | depots: 2 | routes: 6
assistant with no API key: disabled - no API key configured; explanations use the platform's written summaries
```

Note the last line. With no `GEMINI_API_KEY` configured - which is what `.env.example` ships - the plain-language
assistant reports itself disabled and every explanation endpoint serves the platform's own written summaries. The
console does not show an error, and nothing else in the platform changes.

## Pages

| Path | Status |
|---|---|
| `/` | 200 |
| `/overview` | 200 |
| `/risk` | 200 |
| `/decisions` | 200 |
| `/alerts` | 200 |
| `/health` | 200 |
| `/control` | 200 |

## What this does not prove

The simulator starts **paused** (`SIMULATOR_START_MODE=paused` in `.env.example`), so tick 0 is the correct
starting state: a judge presses Run on the Control page, or `curl -X POST localhost:8000/admin/run`. The numbers
in the other evidence reports come from a running world and are reproduced with the scripts named in each.
