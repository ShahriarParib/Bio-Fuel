# Operator API

Interactive reference (OpenAPI): **http://localhost:18080/api/docs**. The console reaches the same API through nginx
at `http://localhost:3000/api`.

## Conventions

- JSON, `snake_case`, times ISO-8601 UTC, litres as numbers, ticks as integers. Everything is SIMULATED data.
- Errors: `{"detail": {"code": "UPPER_SNAKE", "message": "...", "data": {}}}`, e.g. `UNAUTHORIZED` 401,
  `FORBIDDEN` 403, `STALE_DATA` 409, `SLO_CHANGE_FREEZE` 409, `RATE_LIMITED` 429, `ENGINE_UNAVAILABLE` 503.
- Auth: `Authorization: Bearer <token>`. Reads are open; actions need the **operator** token; policy switches need the
  **supervisor** token. Tokens come from `.env` (`API_OPERATOR_TOKEN`, `API_SUPERVISOR_TOKEN`).
- Rate limit: 600 requests/min per client (shared by all api replicas through Redis); 429 carries `Retry-After`.
- `/api/state` sends a content-hash `ETag`; send it back as `If-None-Match` to get a cheap 304 when nothing changed.

## Read

| Method · path | Returns |
|---|---|
| `GET /api/state` | the whole world as the engine sees it: clock, stations, depots, routes, events, supply, in-flight shipments, metrics, risk, cover, forecast status, Impact Meter, SLO, component health, engine/operating mode |
| `GET /api/stream` | Server-Sent Events: `state.updated`, `decision.*`, `alert.*`, `incident.updated`, `mode.changed` |
| `GET /api/recommendations` | recommendations waiting for a human (brief §9 format: impact before → after, explanation, constraints) |
| `GET /api/decisions` · `/api/decisions/{id}` | decision history with lines, statuses and outcomes |
| `GET /api/alerts?status=OPEN\|RESOLVED\|ALL&severity=&category=` | domain alerts + platform (SYSTEM) alerts from Prometheus |
| `GET /api/risk` · `/api/cover` | stockout probability per station × fuel · days/hours of fuel left |
| `GET /api/stations/{id}` · `/api/depots/{id}` | one node's current state (404 `NOT_FOUND` if unknown) |
| `GET /api/audit` | who did what, when |
| `GET /api/system/status` | engine mode and reasons, every component's health (measured from real traffic), data age |
| `GET /api/incidents` | incidents with detect / mitigate / recover timeline and MTTR |
| `GET /api/ping` · `/health/live` · `/health/ready` | liveness, readiness, running version |

## Act (operator token)

| Method · path | Body | Effect |
|---|---|---|
| `POST /api/decisions/{id}/approve` | `{"line_ids"?: [..], "quantities"?: {"<line_id>": litres}}` | approve all or some lines (optionally with edited litres); re-validated against the current world, then executed |
| `POST /api/decisions/{id}/reject` | `{"reason_code", "comment"}` | records why (WRONG_PRIORITY, DATA_LOOKS_WRONG, PREFER_OTHER_ROUTE, NOT_NEEDED, OTHER) |
| `POST /api/plan-now` | `{}` | plan immediately instead of at the next tick |
| `POST /api/control/mode` | `{"mode": "advisory"\|"autopilot"}` | autopilot refuses while data is stale or degraded |
| `POST /api/alerts/{id}/ack` | `{}` | acknowledge a domain alert |
| `POST /api/control/policy` (supervisor) | `{"policy": "optimizer"\|"heuristic", "override": false}` | blocked during an SLO change freeze unless `override` |

## Demo controls (operator token, `DEMO_CONTROLS=true`)

These talk to the simulator's admin API and are audit-logged.

| Method · path | Body |
|---|---|
| `POST /api/demo/sim/run\|pause\|step\|reset` | — (reset also clears faults) |
| `POST /api/demo/events` | `{"preset": "demand_spike"\|"route_cut"\|"single_route_station_cut"\|"supply_delay"\|"supply_shortfall"\|"station_outage"\|"depot_constraint"\|"combined"}` |
| `POST /api/demo/faults` · `/api/demo/faults/clear` · `GET /api/demo/faults` | `{"type": "unavailable"\|"error_rate"\|"latency"\|"stale_data"\|"stream_disconnect", "duration_seconds", "rate"?, "delay_ms"?}`; paused during an SLO change freeze unless supervisor |

## Internal

`POST /api/internal/alertmanager` receives Alertmanager webhooks (internal token) and turns them into SYSTEM alerts.
The engine's own endpoints (`/internal/*` on :8100) are only for the api and require the internal token.
