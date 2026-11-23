"""Chaos proxy: transparent reverse proxy to the simulator that can corrupt JSON responses on demand.

POST /chaos/malform {"resource": "stations", "mode": "drop_field|wrong_type|nan|negative|unknown_enum", "rate": 1.0}
POST /chaos/clear
GET  /chaos/status
Everything else is forwarded unchanged (including SSE, streamed). Only for drills; never in the default stack.
"""
from __future__ import annotations

import json
import os
import random

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

UPSTREAM = os.getenv("CHAOS_UPSTREAM", "http://simulator:8000")
RESOURCES = {"instance": "/v1/instance", "depots": "/v1/depots", "stations": "/v1/stations", "routes": "/v1/routes",
             "events": "/v1/events", "supply_arrivals": "/v1/supply-arrivals", "allocations": "/v1/allocations",
             "metrics": "/v1/metrics", "regions": "/v1/regions"}
FIELDS = {"stations": ("inventory", "status"), "depots": ("inventory", "status"), "routes": ("transit_ticks", "status"),
          "instance": ("tick", "status"), "metrics": ("service_level", "service_level")}

app = FastAPI(title="fsip-chaos-proxy")
client = httpx.AsyncClient(base_url=UPSTREAM, timeout=httpx.Timeout(5.0, read=None))
rules: dict[str, dict] = {}
counts = {"forwarded": 0, "corrupted": 0}


class Malform(BaseModel):
    resource: str = Field(pattern="^(" + "|".join(RESOURCES) + ")$")
    mode: str = Field(pattern="^(drop_field|wrong_type|nan|negative|unknown_enum)$")
    rate: float = Field(default=1.0, ge=0.0, le=1.0)


@app.post("/chaos/malform")
async def malform(body: Malform):
    rules[RESOURCES[body.resource]] = body.model_dump()
    return {"rules": rules}


@app.post("/chaos/clear")
async def clear():
    rules.clear()
    return {"rules": rules}


@app.get("/chaos/status")
async def status():
    return {"upstream": UPSTREAM, "rules": rules, **counts}


def corrupt(data, rule: dict):
    items = data if isinstance(data, list) else [data]
    if not items or not isinstance(items[0], dict):
        return data
    target = items[0]
    num_field, enum_field = FIELDS.get(rule["resource"], (next(iter(target)), next(iter(target))))
    mode = rule["mode"]
    if mode == "drop_field":
        target.pop(num_field, None)
    elif mode == "wrong_type":
        target[num_field] = "not-a-number"
    elif mode == "unknown_enum":
        target[enum_field] = "EXPLODED"
    else:
        bad = float("nan") if mode == "nan" else -1000.0
        val = target.get(num_field)
        if isinstance(val, dict):
            target[num_field] = {k: bad for k in val}
        else:
            target[num_field] = bad
    return data


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def forward(path: str, request: Request):
    url = "/" + path
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    body = await request.body()
    if "text/event-stream" in request.headers.get("accept", "") or path.endswith("stream"):
        req = client.build_request(request.method, url, params=request.query_params, headers=headers, content=body)
        upstream = await client.send(req, stream=True)
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                                 media_type=upstream.headers.get("content-type"),
                                 background=None)
    resp = await client.request(request.method, url, params=request.query_params, headers=headers, content=body)
    counts["forwarded"] += 1
    out_headers = {k: v for k, v in resp.headers.items()
                   if k.lower() not in ("content-length", "content-encoding", "transfer-encoding", "connection")}
    rule = rules.get(url)
    if rule and request.method == "GET" and resp.status_code == 200 and random.random() < rule["rate"]:
        try:
            data = corrupt(resp.json(), rule)
            counts["corrupted"] += 1
            # allow_nan: the point of the "nan" mode is to send a literal NaN the client must reject
            return Response(json.dumps(data, allow_nan=True), status_code=200, headers=out_headers,
                            media_type="application/json")
        except ValueError:
            pass
    return Response(resp.content, status_code=resp.status_code, headers=out_headers)
