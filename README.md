# 🛢️ Fuel Supply Intelligence & Resilience Platform (FSIP)

> **BUP CSE FEST 2026 — Final Hackathon** (powered by Poridhi.io)

An intelligent decision-support platform for a simulated Bangladeshi fuel supply network. The system **observes** the network, **detects** risks, **predicts** shortages, **decides/recommends** allocations, **simulates** their impact, **acts** (with human review), **monitors** itself, and **recovers** from domain crises *and* software failures.

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Our Platform                                  │
│                                                                      │
│  ┌─────────┐    ┌──────────┐    ┌────────────────┐    ┌───────────┐ │
│  │ Frontend │◄──►│   API    │◄──►│     Engine      │◄──►│ Simulator │ │
│  │ React+TS │    │ FastAPI  │    │ Control Loop +  │    │ (Organizer│ │
│  │ :3000    │    │ :8080    │    │ Decision Engine │    │  Docker)  │ │
│  └─────────┘    └──┬───┬───┘    │ :8100           │    │  :8000    │ │
│                     │   │        └───────┬─────────┘    └───────────┘ │
│               ┌─────┘   └─────┐          │                           │
│          ┌────▼───┐     ┌─────▼────┐  ┌──▼───────────┐              │
│          │ Redis  │     │TimescaleDB│  │ Intelligence │              │
│          │ :6379  │     │  :5432    │  │ Forecast+ML  │              │
│          └────────┘     └──────────┘  │ :8200        │              │
│                                        └──────────────┘              │
└──────────────────────────────────────────────────────────────────────┘
              ┌─────────────────────────────────────┐
              │        Observability Stack          │
              │  Prometheus · Grafana · Loki · k6   │
              └─────────────────────────────────────┘
```

### Services

| Service | Purpose | Port |
|---------|---------|------|
| **Simulator** | Organizer's fuel network simulation | 8000 |
| **Engine** | Control loop: sync → detect → predict → decide → act → monitor | 8100 |
| **Intelligence** | ML forecasting, anomaly detection, model registry | 8200 |
| **API** | Operator REST + SSE for the frontend | 18080 |
| **Frontend** | React operator console | 3000 |
| **TimescaleDB** | Time-series history, decisions, alerts | 15432 |
| **Redis** | Live state cache, pub/sub, streams | 16379 |

### Control Loop

```
Every tick: SYNC → DETECT → PREDICT → RISK → DECIDE → SIMULATE → ACT → MONITOR → RECOVER
```

## 🚀 Quick Start

```bash
# 1. Clone
git clone git@github.com:ShahriarParib/Bio-Fuel.git
cd Bio-Fuel

# 2. Configure
cp .env.example .env

# 3. Run the full stack
docker compose up -d --build

# 4. Verify
bash scripts/smoke_test.sh
# → all services should show OK

# 5. Open the operator console
open http://localhost:3000
```

### With Observability (Prometheus + Grafana)

```bash
docker compose --profile obs up -d --build
# Grafana: http://localhost:13001 (admin / change-me)
```

## 📊 Intelligence Layer

| Capability | Method | Purpose |
|------------|--------|---------|
| **Forecasting** | Seasonal profile + LightGBM | Predict demand 48 ticks ahead |
| **Risk Scoring** | Monte Carlo (N=300) | Stockout probability + ETA per station/fuel |
| **Anomaly Detection** | CUSUM + conformal | Demand shifts, inventory variance, supply delays |
| **Decision Engine** | LP optimizer (PuLP/CBC) | Optimal allocation with zero waste |
| **Shadow Twin** | Lightweight sim replica | What-if impact estimation |
| **Fallback** | Greedy heuristic | Auto-activates when intelligence service is down |

### Key Results (measured against real simulator)

- **Service Level ≥ 0.99** over 3 simulated days (baseline)
- **0 allocation failures, 0 overflow waste**
- LP optimizer beats greedy by **+2.9 to +8.1 pts** for worst-station service level after supply ends
- Scarcity-aware cross-region balancing for extended scenarios

## 🛡️ Resilience

The platform handles **every** failure mode the organizers can inject:

| Fault | Response | Recovery |
|-------|----------|----------|
| Simulator unavailable | Circuit breaker → cached state → SAFE_HOLD | Auto half-open probe |
| High error rate | Retry with backoff + jitter | Breaker resets after 15s |
| High latency | 2s timeout, 3 retries | Degraded mode on screen |
| Stale data | Detect `x-simulator-stale` header | DATA_QUALITY alert |
| SSE disconnect | Auto-reconnect + poll fallback | Seamless switch |
| Intelligence down | Fallback seasonal forecaster | Heuristic allocator |
| DB down | Redis-only degraded mode | Auto-reconnect |

**Chaos scripts** in `scripts/chaos/` reproduce each scenario.

## 📈 Observability

- **Prometheus** metrics on every service (`/metrics`)
- **Grafana** dashboards: service health, domain KPIs, intelligence performance, SLOs
- **Structured JSON logging** (structlog) → Loki via Promtail
- **Alert rules**: target down, high error rate, service level drop, stockout risk
- **k6 load testing** with p50/p95/p99 latency reports

## 🧪 Testing

```bash
# Python unit tests
make test

# Frontend tests
make fe-test

# Lint
make lint

# Full compose + smoke
make up && make smoke
```

## 📁 Project Structure

```
fuel-platform/
├── docker-compose.yml          # Full stack
├── Makefile                    # Developer commands
├── .env.example                # Configuration template
├── libs/fsip_common/           # Shared Python models & utilities
├── services/
│   ├── engine/                 # Control loop + decision engine
│   ├── intelligence/           # ML forecasting + anomaly detection
│   └── api/                    # Operator REST + SSE API
├── frontend/                   # React + TypeScript + Tailwind
├── observability/              # Prometheus, Grafana, Loki configs
├── evaluation/                 # Policy comparison harness
├── loadtest/                   # k6 performance tests
├── scripts/                    # Chaos, demo, smoke test scripts
└── docs/                       # Architecture, evidence, demo runbook
```

## 🎯 Judging Criteria Coverage

| Criterion | Weight | Our Approach |
|-----------|--------|-------------|
| Working Product & UX | 20% | Polished operator console with live data, risk board, recommendation inbox |
| Intelligence & Decision Quality | 20% | Forecast + Monte Carlo risk + LP optimizer + measured comparison |
| Architecture & Integration | 15% | Clean microservices, resilient simulator client, idempotent writes |
| DevOps & Engineering Quality | 15% | Docker Compose, CI/CD, automated tests, versioned images |
| Resilience & Incident Response | 15% | Full failure matrix, circuit breakers, fallback chain, chaos scripts |
| Observability & Performance | 10% | Prometheus + Grafana + Loki + k6 load test report |
| Demo & Problem Understanding | 5% | 14-step scripted demo with crisis scenarios |

## 🔑 Key Design Decisions

1. **REST as truth, SSE as hint** — engine polls REST for verified state, SSE only triggers faster cycles
2. **Single writer** — only the engine writes to the simulator (no distributed locking)
3. **Zero waste** — projected inventory accounting includes in-flight shipments, prevents overflow
4. **Human in the loop** — recommendations require approval; autopilot only for low-risk routine top-ups
5. **Layered fallbacks** — every component has a degraded mode that keeps operations running

## 👥 Team

| Name | GitHub |
|------|--------|
| Shahriar Parib | [@ShahriarParib](https://github.com/ShahriarParib) |

## 📜 License

This project was built for the BUP CSE FEST 2026 Hackathon. All simulator interactions use the organizer-provided Docker image (`asifmahmoud414/bup-fuel-supply-simulator:1.0.0`).

> **SIMULATED ENVIRONMENT — NOT REAL FUEL INFRASTRUCTURE**
