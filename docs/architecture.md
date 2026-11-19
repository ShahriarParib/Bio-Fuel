# 03: Architecture

> **v2 update:** read `13_ARCHITECTURE_V2.md` right after this file. It changes Postgres → TimescaleDB, pub/sub → Redis
> Streams, adds notifier/Mailpit/Jaeger, JWT roles, feature flags, break-glass and the ledger, and **pins `pulp==2.9.0`**.
> Where the two files differ, v2 wins.

## 1. Big picture

```mermaid
flowchart LR
  subgraph World["Organizer world"]
    SIM[BUP Fuel Simulator<br/>:8000 REST + SSE]
  end

  subgraph Core["Our platform"]
    ENG[engine<br/>Python worker :8100<br/>sync · risk · decide · act]
    INT[intelligence<br/>FastAPI :8200<br/>forecast · anomaly · model registry]
    API[api<br/>FastAPI :8080<br/>operator REST + SSE]
    PG[(postgres :5432<br/>history · decisions · alerts)]
    RD[(redis :6379<br/>live state cache + pub/sub)]
    FE[frontend<br/>React + nginx :3000]
  end

  subgraph Obs["Observability"]
    PROM[prometheus :9090]
    GRAF[grafana :3001]
    LOKI[loki :3100]
    PTAIL[promtail]
    AM[alertmanager :9093]
  end

  SIM -- REST (truth) --> ENG
  SIM -- SSE (hint) --> ENG
  ENG -- POST /v1/allocations --> SIM
  ENG -- HTTP + timeout + breaker --> INT
  ENG -- write --> PG
  ENG -- publish state/alerts --> RD
  API -- read --> RD
  API -- read/write --> PG
  API -- approve/reject cmds --> ENG
  FE -- /api + SSE --> API
  PROM -- scrape /metrics --> ENG & INT & API
  PTAIL -- docker logs --> LOKI
  GRAF --> PROM & LOKI
  PROM --> AM
```

### Why this split (short)
- **engine** is the single writer to the simulator. One process runs the control loop, so there are no
  double-allocations and no distributed locking.
- **intelligence** is separate so it can fail independently. That is exactly the brief's "prediction service
  becomes unavailable" inject. The engine has a built-in fallback forecaster.
- **api** is stateless (reads Redis/Postgres), so it can scale horizontally and absorb "API traffic increases"
  without touching the control loop.
- **Redis** holds the latest world snapshot + computed risk so the api can answer in a few ms and push live
  updates via pub/sub.
- **Postgres** holds durable history: snapshots, decisions, approvals, alerts, incidents, model registry, eval runs.

## 2. Tech stack (fixed; do not change)

| Concern | Choice | Version guidance |
|---|---|---|
| Backend language | Python | 3.12 |
| Web framework | FastAPI + Uvicorn | fastapi ≥0.115, uvicorn[standard] |
| HTTP client | httpx (async) | ≥0.27 |
| SSE client | httpx-sse | ≥0.4 |
| Validation | pydantic v2 + pydantic-settings | |
| DB | PostgreSQL 16 + SQLAlchemy 2 (async) + asyncpg + Alembic | |
| Cache/pubsub | Redis 7 + redis-py (asyncio) | |
| Retry/circuit | tenacity + own small CircuitBreaker class | |
| ML | numpy, pandas, scikit-learn, lightgbm, joblib | |
| Optimizer | PuLP (bundled CBC) + highspy | **pin `pulp==2.9.0`** (4.x has a different API and no bundled solver) |
| Metrics | prometheus-client | |
| Logging | structlog → JSON to stdout | |
| LLM (optional) | anthropic SDK; model from env `LLM_MODEL` (default `claude-haiku-5-5`) | only if `ANTHROPIC_API_KEY` is set |
| Frontend | React 18 + TypeScript + Vite + TailwindCSS + Recharts + TanStack Query + React Router | Node 20+ |
| Frontend serving | nginx (alpine), reverse-proxy `/api` → api:8080 | |
| Observability | Prometheus, Grafana 11, Loki + Promtail, Alertmanager | |
| Load test | k6 (grafana/k6 docker image) | |
| CI | GitHub Actions | |
| Lint/format | ruff, mypy (lenient), eslint, prettier | |
| Tests | pytest, pytest-asyncio, respx (httpx mocking), Vitest (frontend) | |
| Packaging | uv or pip with `requirements.txt` per service (use pip + requirements.txt for simplicity) | |

## 3. Repository layout (create exactly this)

```
fuel-platform/                      # repo root (create inside /home/iftakhar/poridhi_intern_initial/)
├── README.md
├── Makefile
├── docker-compose.yml              # full stack
├── docker-compose.override.yml     # dev: hot reload mounts (optional)
├── .env.example
├── .gitignore
├── .github/workflows/ci.yml
├── libs/
│   └── fsip_common/                # shared python package (installed into each service image)
│       ├── pyproject.toml
│       └── fsip_common/
│           ├── __init__.py
│           ├── models.py           # pydantic models of simulator entities + our domain models
│           ├── logging.py          # structlog setup
│           ├── settings.py         # base settings
│           └── timeutil.py         # tick <-> sim_time helpers
├── services/
│   ├── engine/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── alembic.ini, migrations/        # engine owns DB migrations
│   │   ├── app/
│   │   │   ├── main.py                     # FastAPI app (health/metrics/internal cmds) + starts loops
│   │   │   ├── config.py
│   │   │   ├── sim/client.py               # resilient simulator client
│   │   │   ├── sim/sse.py                  # SSE listener
│   │   │   ├── sim/validation.py           # response validation + quarantine
│   │   │   ├── state/store.py              # in-memory WorldState + Redis publish
│   │   │   ├── state/sync.py               # sync loop (tick detection, reset detection)
│   │   │   ├── intelligence/client.py      # calls intelligence svc with breaker
│   │   │   ├── intelligence/fallback.py    # built-in seasonal forecaster
│   │   │   ├── risk/projection.py          # projected inventory timelines
│   │   │   ├── risk/stockout.py            # time-to-stockout + probability
│   │   │   ├── detect/anomaly.py           # anomaly + disruption detectors
│   │   │   ├── decision/heuristic.py       # greedy allocator (fallback)
│   │   │   ├── decision/optimizer.py       # LP allocator
│   │   │   ├── decision/constraints.py     # shared feasibility checks
│   │   │   ├── decision/twin.py            # shadow twin for what-if
│   │   │   ├── decision/engine.py          # orchestrates: plan → recommend → approve/auto → execute
│   │   │   ├── decision/executor.py        # posts allocations idempotently, tracks outcomes
│   │   │   ├── explain/explainer.py        # structured explanations + LLM narrative
│   │   │   ├── alerts/manager.py           # alert lifecycle (open/ack/resolve), dedupe
│   │   │   ├── db/models.py, db/session.py, db/repo.py
│   │   │   └── observability/metrics.py
│   │   └── tests/
│   ├── intelligence/
│   │   ├── Dockerfile, requirements.txt
│   │   ├── app/main.py                     # /forecast, /anomaly/score, /models, /health, /metrics
│   │   ├── app/models/seasonal.py          # seasonal-profile model
│   │   ├── app/models/gbm.py               # LightGBM model
│   │   ├── app/registry.py                 # model versioning (files + metadata json)
│   │   ├── training/generate_dataset.py    # runs the simulator via /admin/step to build history
│   │   ├── training/train.py               # trains + backtests + registers
│   │   ├── artifacts/                      # versioned model files (committed small, or built in image)
│   │   └── tests/
│   └── api/
│       ├── Dockerfile, requirements.txt
│       ├── app/main.py                     # operator REST + SSE (/api/*)
│       ├── app/routers/{state,risk,alerts,recommendations,decisions,system,assistant,whatif,control}.py
│       ├── app/auth.py                     # simple role auth (viewer/operator) via API key/JWT
│       └── tests/
├── frontend/
│   ├── Dockerfile, nginx.conf, package.json, vite.config.ts, tailwind.config.js
│   └── src/{main.tsx, App.tsx, api/, pages/, components/, hooks/, lib/}
├── observability/
│   ├── prometheus/prometheus.yml, prometheus/alerts.yml
│   ├── alertmanager/alertmanager.yml
│   ├── grafana/provisioning/{datasources,dashboards}/…, grafana/dashboards/*.json
│   ├── loki/loki-config.yml
│   └── promtail/promtail-config.yml
├── evaluation/
│   ├── scenarios/*.yaml                    # our crisis scenarios (events to inject + duration)
│   ├── harness.py                          # replay scenarios × policies, write report
│   └── reports/                            # generated markdown/json/csv
├── loadtest/
│   ├── k6/{dashboard_read.js, decision_flow.js, spike.js}
│   └── run.sh
├── scripts/
│   ├── chaos/{sim_unavailable.sh, sim_error_rate.sh, sim_latency.sh, sim_stale.sh, sse_disconnect.sh,
│   │          kill_intelligence.sh, kill_db.sh, malformed_proxy.sh, clear.sh}
│   ├── demo/{reset.sh, crisis_demand_spike.sh, crisis_combined.sh, step.sh}
│   └── smoke_test.sh
├── chaos-proxy/                            # tiny proxy that can corrupt simulator responses (malformed-field drill)
└── docs/
    ├── architecture.md, architecture.png
    ├── assumptions.md, decisions.md (ADRs)
    ├── demo-runbook.md
    ├── api.md
    └── evidence/{resilience.md, loadtest-report.md, observability.md, evaluation.md, screenshots/}
```

## 4. Services, ports, health

| Service | Container name | Port (host:container) | Health endpoint | Depends on |
|---|---|---|---|---|
| simulator | fsip-simulator | 8000:8000 | GET /v1/health | |
| timescaledb (v2, see 20) | fsip-timescaledb | 15432:5432 | `pg_isready` | |
| redis | fsip-redis | 6379:6379 | `redis-cli ping` | |
| intelligence | fsip-intelligence | 8200:8200 | GET /health/live, /health/ready | |
| engine | fsip-engine | 8100:8100 | GET /health/live, /health/ready | simulator, postgres, redis (intelligence optional) |
| api | (no container_name, see 20) | 18080:8080 | GET /health/live, /health/ready | postgres, redis, engine (soft) |
| frontend | fsip-frontend | 3000:80 | GET / | api |
| chaos-proxy | fsip-chaos-proxy | 8001:8001 | GET /health | simulator (only used in the malformed-data drill) |
| prometheus | fsip-prometheus | 9090:9090 | | |
| grafana | fsip-grafana | 3001:3000 | | prometheus, loki |
| loki | fsip-loki | 3100:3100 | | |
| promtail | fsip-promtail | | | loki |
| alertmanager | fsip-alertmanager | 9093:9093 | | |

All containers: `restart: unless-stopped`, healthchecks, resource limits (`deploy.resources.limits`), JSON logs,
and the label `logging=promtail`.

## 5. Environment variables (`.env.example`)

```dotenv
# --- simulator ---
SIMULATION_SPEED=0.5
TICK_MINUTES=15
SIMULATOR_START_MODE=paused
SIM_BASE_URL=http://simulator:8000          # engine uses this; chaos drill switches to http://chaos-proxy:8001

# --- engine ---
ENGINE_MODE=advisory                         # advisory | autopilot
ENGINE_POLICY=optimizer                      # optimizer | heuristic
ENGINE_DECISION_INTERVAL_TICKS=1             # run planner at most once per N ticks
ENGINE_POLL_INTERVAL_SEC=1.0                 # fallback polling when SSE is down
SIM_TIMEOUT_SEC=2.0
SIM_MAX_RETRIES=3
BREAKER_FAILURE_THRESHOLD=5
BREAKER_RESET_SEC=15
AUTO_APPROVE_MAX_LITERS=7000                 # autopilot only auto-executes lines <= this
AUTO_APPROVE_MIN_CONFIDENCE=0.7
DEPOT_RESERVE_FRACTION=0                     # benchmark: permanent reserves strand fuel; reserve only via supervisor in scarcity
STATION_TARGET_FILL=0.90
PLANNING_HORIZON_TICKS=96                    # benchmark: 96 beats 48 after supply ends
INTELLIGENCE_URL=http://intelligence:8200
INTELLIGENCE_TIMEOUT_SEC=1.5

# --- shared infra ---
POSTGRES_USER=fsip
POSTGRES_PASSWORD=change-me
POSTGRES_DB=fsip
DATABASE_URL=postgresql+asyncpg://fsip:change-me@postgres:5432/fsip
REDIS_URL=redis://redis:6379/0

# --- api ---
API_OPERATOR_TOKEN=change-me-operator        # required for approve/reject/control actions
API_RATE_LIMIT_PER_MIN=600
ENGINE_INTERNAL_URL=http://engine:8100
ENGINE_INTERNAL_TOKEN=change-me-internal

# --- LLM (optional) ---
ANTHROPIC_API_KEY=
LLM_MODEL=claude-haiku-5-5
LLM_TIMEOUT_SEC=8

# --- observability ---
GRAFANA_ADMIN_PASSWORD=change-me
```

## 6. Control loop (engine) at a glance

```
every new tick (SSE simulation.tick or poll detects tick change):
  1. SYNC     GET instance, depots, stations, routes, events, supply-arrivals, allocations (parallel)
              + demand-history (last 3 ticks, only the new rows)
              validate → if invalid: quarantine, alert, keep last-good, mark DEGRADED
  2. DETECT   anomaly detectors on new demand rows / inventory deltas / supply status / events
  3. PREDICT  forecast (intelligence svc → fallback on failure) for 48 ticks ahead
  4. RISK     projected inventory per (station,fuel) incl. in-flight + scheduled arrivals → stockout ETA + prob
  5. DECIDE   optimizer (→ heuristic fallback on timeout/error) → candidate allocation lines
  6. SIMULATE shadow twin: risk before vs after for each recommendation → expected impact
  7. ACT      autopilot & low-risk → execute; else → recommendation inbox (PENDING_APPROVAL)
  8. MONITOR  publish snapshot to Redis, write DB rows, update Prometheus metrics, raise/resolve alerts
  9. RECOVER  breakers/half-open probes, SSE reconnect, outcome tracking of executed allocations
```
Coalescing rule: if a cycle is running when new ticks arrive, remember only the latest tick and run one more cycle
afterwards. Never queue more than one.

## 7. Data flow to the UI
- engine publishes `fsip:state` (full JSON snapshot) and appends to the stream `fsip:domain-events` (v2) in Redis after each cycle.
- api exposes `GET /api/state` (from Redis), and `GET /api/stream` (SSE to browsers, fed from Redis pub/sub).
- If Redis is down: api falls back to reading the last snapshot from Postgres (`world_snapshots`), marks the
  response `degraded: true`.
