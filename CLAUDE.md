# CLAUDE.md: session rules for fuel-platform (FSIP)

Fuel Supply Intelligence & Resilience Platform for the BUP CSE FEST 2026 final hackathon. Deadline **Thu 2026-11-26**.
A working, demoable `main` beats any unfinished feature.

## Where the plan lives (outside this repo)
`../fuel-plan-extracted/fuel-platform-plan/`
- `26_FINAL_SPRINT_PLAN.md`: **what to build now** (blocks B1-B8, extensions E1-E9, freeze protocol). Wins on scope/order/cuts.
- `20_FINAL_SPEC_LOCK.md`: names, versions, ports, enums, parameters. Wins on naming/contracts.
- Then `24` > `21` (API contracts) > `23` (module specs) > `22` (UI spec) > `13` > `phases/*.md` (task specs).
- `02_SIMULATOR_FACTS.md`: verified simulator behaviour. Do not invent simulator behaviour; test it and record it.
- `27_HUMAN_EDGE_HOOKS.md`: private strategy. **Never copy it into this repo** (the repo is public).
- `reference/`: tested code (sim client, analytics, greedy + LP benchmark). Adapt it; do not rewrite from scratch.

## How a session runs
1. The user says "Execute B<N>". Read the block in `26`, then only the spec sections it references.
2. Write a short plan (files to touch, tests to add), then implement task by task.
3. Run: the block's tests, `make test`, `make lint`, `npm test` (if frontend changed), `make smoke` (if services changed),
   and every "Done when" command of the block. Paste short real outputs into `PROGRESS.md`.
4. Append an entry to `docs/ENGINEERING_JOURNAL.md` (template at the bottom of that file): what, how, tools, evidence,
   problems + fixes, judging criterion.
5. Commit `bN: <what>` (one logical change per commit), push. Report: done / deviations / still red.
6. Overrun by 50% → drop the block's last bullet, log a deviation in `docs/assumptions.md`, move on.

## Hard rules
- Only the engine writes to the simulator (`POST /v1/allocations`, cancel). `/admin/*` only in tests, the evaluation
  harness, chaos scripts and the demo Control endpoints behind `DEMO_CONTROLS=true` + operator token.
- Every allocation passes `decision/constraints.py` (incl. in-flight overflow guard with p10 demand, routes disrupted
  or scheduled-disrupted at departure, per-depot dispatch cap across all fuels). Never write on stale or invalid data.
- Every network call has a timeout. No bare `except`. structlog, no `print`. Pure functions for maths (testable without I/O).
- Health of a dependency is measured from real traffic (errors, breaker, stale header), never from `/v1/health` alone.
- No secrets in git: `.env` is ignored; add new variables to `.env.example` with a safe default.
- Exact names from `20` (services, env vars, metrics `fsip_*`, enums, error body `{"detail":{"code","message"}}`).
- Pinned versions; do not upgrade. Images run Python 3.12; local venv may be 3.11.
- UI labels everything as SIMULATED; consequential actions need human approval unless the autopilot gate passes.

## Environment
- Windows 11 host; **Docker Engine 29 + Compose v5 run inside WSL Ubuntu 24.04** (no Docker Desktop; distro disk at
  `D:\WSL\Ubuntu`, `.wslconfig` = 10 GB RAM / 8 CPU). There is no `docker` on the Windows PATH: run everything through
  WSL, e.g. `wsl -d Ubuntu -- bash -lc "cd '/mnt/d/Fuel Project/fuel-platform' && make up"`. Published ports are
  reachable from Windows at `localhost`.
- Line endings are forced to LF by `.gitattributes` (Windows git has `core.autocrlf=true`); keep it that way or the
  bash scripts and Makefile break in WSL/containers.
- Long multi-command shell work from PowerShell: write a script file and run it with `bash` inside WSL (PowerShell
  mangles `$var` passed through `wsl ... bash -c`).
- Core: `make up`, `make smoke`, `make test`, `make lint`. Observability: `make obs-up` (Grafana :13001).
- UI :3000, api :18080 (or `:3000/api`), engine :8100, intelligence :8200, simulator :8000 (`/docs`, `/admin`).
