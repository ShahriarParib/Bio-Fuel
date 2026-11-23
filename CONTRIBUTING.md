# Contributing

## Branches

Every change, however small, follows the same path:

1. Branch from `dev`: `git switch dev && git pull && git switch -c feature/<name>` (or `fix/<name>`, `chore/<name>`).
2. Commit there; run the checks below; push. CI runs on the branch.
3. Merge into `dev` with `--no-ff`, rebuild the stack from `dev` and run the full check (unit tests, `make smoke`,
   `evaluation/e2e_autopilot.py`). Push `dev`.
4. Only when `dev` is fully green: merge `dev` into `main` with `--no-ff`. **`main` is always demoable.**
5. Releases are tags on `main` (`vX.Y.Z`); CI publishes the tested images to GHCR and creates a GitHub Release.

## Checks before pushing

| What | Command |
|---|---|
| Python lint | `make lint` (ruff) |
| Python unit tests | `make test` |
| Frontend | `cd frontend && npx tsc --noEmit -p . && npm test` |
| Stack health (if services changed) | `make up && make smoke` |
| Autopilot end to end | `.venv/bin/python evaluation/e2e_autopilot.py --ticks 192 --assert-sl 0.98` |
| Metric contract (if metrics, alerts or dashboards changed) | `bash scripts/check_metrics.sh` |
| Failure drills (if resilience code changed; injects real failures) | `make chaos` |

Local setup: `make venv` (Python 3.12 venv with the shared library installed editable), `cd frontend && npm ci`.

## Rules the code relies on

- **One writer.** Only the engine writes to the simulator (`POST /v1/allocations`). `/admin/*` is used only by tests,
  the evaluation harness, the chaos drills and the demo Control endpoints.
- **Every shipment passes `services/engine/app/decision/constraints.py`**, whatever produced it (optimizer, heuristic,
  a human editing litres). Never ship on stale or invalid data.
- **Health comes from real traffic** (errors, latency, breaker, stale header), never from `/v1/health` alone.
- Every network call has a timeout; no bare `except`; structured logs (structlog), no `print`; maths in pure
  functions with unit tests.
- Metric names start with `fsip_`; API errors are `{"detail": {"code", "message"}}`.
- No secrets in git: `.env` is ignored; new variables go into `.env.example` with a safe default.
- Pinned versions everywhere (Python, npm, images). Line endings are LF (`.gitattributes`); keep it that way or the
  shell scripts break in containers.
- Every deviation from the original design and its reason goes into `docs/assumptions.md`; every finished block gets
  an entry in `docs/ENGINEERING_JOURNAL.md` (what, how, evidence, problems and fixes).
