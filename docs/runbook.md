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

## Run on Kubernetes (optional)

Compose is the demo path; Kubernetes is the operational one -- it replaces a dead component and scales the read
path without anyone watching. One single-node cluster runs on the laptop (kind), from the chart in
`deploy/helm/fsip`.

| Task | Command |
|---|---|
| validate the chart (no cluster needed) | `make k8s-lint` |
| create cluster + install | `make k8s-up` |
| console / simulator | http://localhost:3080 / http://localhost:3800 |
| pods, autoscaler, services | `make k8s-ps` |
| the two proofs -> `docs/evidence/kubernetes.md` | `make k8s-demo` |
| uninstall release, keep cluster | `make k8s-down keep=1` |
| delete the cluster | `make k8s-down` |

Deliberate constraints encoded in the chart: the engine is `replicas: 1` with `strategy: Recreate` (it is the only
writer to the simulator, so two must never run at once), the api autoscales 2-6 on 60% CPU (the number the load
test measured), and the break-glass page is served by a sidecar so it stays readable while the engine restarts.

For a real cluster, point the chart at the published images and set your own tokens:

```bash
helm upgrade --install fsip deploy/helm/fsip -n fsip --create-namespace   --set image.registry=ghcr.io/shahriarparib --set image.pullPolicy=IfNotPresent   --set secrets.operatorToken=... --set secrets.supervisorToken=...   --set secrets.internalToken=... --set secrets.postgresPassword=...
```

## The plain-language assistant

Optional. With `GEMINI_API_KEY` empty, every "Explain in plain words" button still works and serves the platform's
own written summary — nothing breaks, and the badge on System Health says the assistant is off.

| Task | Command |
|---|---|
| turn it on | put `GEMINI_API_KEY=...` in `.env`, then `docker compose up -d api` |
| turn it off (demo without a network) | clear `GEMINI_API_KEY` in `.env`, `docker compose up -d api` |
| check its state | `curl -s localhost:3000/api/assistant \| jq` |
| regenerate the evidence | `.venv/bin/python scripts/demo/assistant_evidence.py` |
| clear cached explanations | `docker compose exec redis sh -c "redis-cli --scan --pattern 'fsip:llm:*' \| xargs -r redis-cli del"` |

If the badge says *cooling down*, the provider failed three times in a row and is being asked once a minute instead
of once a click. Explanations keep working from the written summaries meanwhile; no action is needed.

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

`.env` only (never committed). Operator, supervisor and internal tokens, and the Grafana admin password. Change every `change-me-*` value before exposing the console beyond localhost.
