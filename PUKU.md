# PUKU.md - Poridhi Puku CLI Instructions for FSIP

Welcome to the **Fuel Supply Intelligence & Resilience Platform (FSIP)** — BUP CSE FEST 2026 Final Hackathon.
This file provides contextual instructions and shortcuts for **Poridhi Puku CLI** (`puku` / `puku-cli`).

## Project Overview

FSIP is an enterprise-grade decision-support and autonomous resilience platform for the simulated Bangladeshi fuel network (`asifmahmoud414/bup-fuel-supply-simulator:1.0.0`).
- **Core Architecture**: Engine (LP + Heuristic), Intelligence (Seasonal forecaster + Monte Carlo), API (Stateless FastAPI), React Operator Console (Port 3000), TimescaleDB, and Redis.
- **Resilience**: 11/11 automated chaos drills passing, circuit breakers, safe-hold mode, break-glass static console.
- **Key Invariant**: 0 failed shipments, 0 litres lost to overflow.

## Environment & Run Commands

The runtime runs via Docker inside WSL2 Ubuntu:

```bash
# Smoke test and health check
bash scripts/smoke_test.sh

# Run all 240 unit & contract tests
make test

# Run resilience / chaos engineering drills
make chaos

# Start Kubernetes demo on kind cluster
make k8s-up && make k8s-demo

# Fresh-clone reproduction test (15-second verification)
bash scripts/clean_clone_test.sh
```

## Useful Puku CLI Prompts for Operators & SRE

When running `puku` inside this repository:

1. **Health & Triage:**
   `puku "Run scripts/smoke_test.sh and explain any degraded services or breaker states."`

2. **Incident Investigation:**
   `puku "Inspect the latest incident log from the engine and check why circuit breaker tripped."`

3. **Metrics & Verification:**
   `puku "Run bash scripts/check_metrics.sh and verify Prometheus metric contracts."`

4. **Codebase Exploration:**
   `puku "Explain how decision/constraints.py protects against in-flight arrival overflow."`

## Engineering Rules
- Working branches are cut from `dev`. Changes merge to `dev` with `--no-ff`, verified, then merged to `main`.
- Line endings are strictly LF (`.gitattributes`).
- Secrets and tokens are kept in `.env` (gitignored).
