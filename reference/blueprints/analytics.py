"""Tested reference implementations (pure python + numpy) for the executing AI to adapt.
Run the self-test:  python3 -I reference/blueprints/analytics.py
Copy into: libs/fsip_common/ (conformal, fairness, ledger) and services/engine/app/{risk,detect}/ (stockout, cusum).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

import numpy as np


# --------------------------------------------------------------------------- 1. arrival timing
def expected_arrival_tick(created_tick: int, transit_ticks: int) -> int:
    """VERIFIED on simulator 1.0.0: created at T (PENDING) -> departs in step T->T+1 (departure_tick=T)
    -> expected_arrival_tick = T + transit; the fuel is in station inventory when the instance shows tick
    T + transit + 1, i.e. it can serve demand of tick index >= T + transit (approximately)."""
    return created_tick + transit_ticks


# --------------------------------------------------------------------------- 2. Monte Carlo stockout
def stockout_probability(inv0: float, capacity: float, p10: np.ndarray, p50: np.ndarray, p90: np.ndarray,
                         arrivals: np.ndarray, lead_ticks: int, n: int = 300, seed: int = 0,
                         outage: np.ndarray | None = None) -> dict:
    """Per-tick demand ~ asymmetric normal around p50 (sigma from p10/p90), plus a shared level shock.
    arrivals[k] = liters arriving at offset k. Returns probabilities + p50 path stockout time."""
    H = len(p50)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n, H))
    sig_hi = np.maximum(p90 - p50, 1e-6) / 1.2816
    sig_lo = np.maximum(p50 - p10, 1e-6) / 1.2816
    d = p50 + np.where(z > 0, z * sig_hi, z * sig_lo)
    d *= rng.normal(1.0, 0.05, size=(n, 1))            # correlated level uncertainty
    d = np.clip(d, 0, None)
    if outage is not None:
        d = np.where(outage[None, :], 0.0, d)            # demand at an OUTAGE station is lost, not served
    inv = np.full(n, float(inv0))
    first_out = np.full(n, H + 1)
    for k in range(H):
        inv = np.minimum(capacity, inv + arrivals[k]) - d[:, k]
        hit = (inv <= 0) & (first_out > H)
        first_out[hit] = k
        inv = np.maximum(inv, 0)
    # deterministic p50 path
    inv_p, t50 = float(inv0), None
    for k in range(H):
        inv_p = min(capacity, inv_p + arrivals[k]) - p50[k]
        if inv_p <= 0 and t50 is None:
            t50 = k
        inv_p = max(inv_p, 0)
    return {"p_stockout_lead": float((first_out <= lead_ticks).mean()),
            "p_stockout_horizon": float((first_out <= H).mean()),
            "ticks_to_stockout_p50": t50}


# --------------------------------------------------------------------------- 3. CUSUM (two-sided)
@dataclass
class Cusum:
    k: float = 0.5      # slack in sigma units
    h: float = 5.0      # decision threshold in sigma units
    pos: float = 0.0
    neg: float = 0.0

    def update(self, z: float) -> str | None:
        self.pos = max(0.0, self.pos + z - self.k)
        self.neg = max(0.0, self.neg - z - self.k)
        if self.pos > self.h:
            self.pos = 0.0
            return "UP"
        if self.neg > self.h:
            self.neg = 0.0
            return "DOWN"
        return None


# --------------------------------------------------------------------------- 4. conformal + ACI
@dataclass
class AdaptiveConformal:
    """Split-conformal absolute-residual band with Adaptive Conformal Inference (Gibbs & Candes 2021)."""
    alpha: float = 0.2          # target miscoverage -> 80% interval
    gamma: float = 0.005
    window: int = 192
    residuals: list = field(default_factory=list)
    alpha_t: float = 0.2

    def band(self) -> float | None:
        r = self.residuals[-self.window:]
        if len(r) < 48:
            return None
        a = min(max(self.alpha_t, 0.001), 0.999)
        q = math.ceil((len(r) + 1) * (1 - a)) / len(r)
        return float(np.quantile(np.abs(r), min(q, 1.0)))

    def update(self, y: float, yhat: float) -> None:
        b = self.band()
        if b is not None:
            err = 1.0 if abs(y - yhat) > b else 0.0
            self.alpha_t += self.gamma * (self.alpha - err)
        self.residuals.append(y - yhat)


# --------------------------------------------------------------------------- 5. fairness
def jain_index(values) -> float:
    v = np.asarray(list(values), dtype=float)
    return float(v.sum() ** 2 / (len(v) * (v ** 2).sum())) if len(v) and (v ** 2).sum() > 0 else 1.0


# --------------------------------------------------------------------------- 6. hash-chained ledger
def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def ledger_hash(prev_hash: str, row: dict) -> str:
    return hashlib.sha256((prev_hash + canonical(row)).encode()).hexdigest()


def ledger_verify(rows: list[dict]) -> dict:
    """rows ordered by id, each {id, at, actor, action, payload, prev_hash, hash}"""
    prev = "GENESIS"
    for i, r in enumerate(rows):
        body = {k: r[k] for k in ("id", "at", "actor", "action", "payload")}
        if r["prev_hash"] != prev or ledger_hash(prev, body) != r["hash"]:
            return {"ok": False, "broken_at_index": i, "id": r["id"]}
        prev = r["hash"]
    return {"ok": True, "length": len(rows), "head": prev}


# --------------------------------------------------------------------------- 7. SLO burn rate
def burn_rate(error_ratio: float, slo: float) -> float:
    """burn rate = observed error ratio / error budget ratio (1 - SLO)"""
    return error_ratio / max(1e-9, 1 - slo)


# --------------------------------------------------------------------------- self-test
if __name__ == "__main__":
    assert expected_arrival_tick(5, 2) == 7
    H = 48
    p50 = np.full(H, 100.0); p10 = p50 * 0.9; p90 = p50 * 1.1
    lo = stockout_probability(1000, 15000, p10, p50, p90, np.zeros(H), lead_ticks=4)
    hi = stockout_probability(9000, 15000, p10, p50, p90, np.zeros(H), lead_ticks=4)
    assert lo["p_stockout_horizon"] > 0.9 and lo["ticks_to_stockout_p50"] == 9, lo
    assert hi["p_stockout_horizon"] < 0.05, hi
    arr = np.zeros(H); arr[5] = 5000
    mid = stockout_probability(1000, 15000, p10, p50, p90, arr, lead_ticks=4)
    assert mid["p_stockout_horizon"] < lo["p_stockout_horizon"]
    c = Cusum(); sig = [0.0] * 10 + [1.5] * 10
    alarms = [i for i, z in enumerate(sig) if c.update(z)]
    assert alarms and alarms[0] <= 15, alarms               # detects a +1.5 sigma shift within ~5 ticks
    rng = np.random.default_rng(1); ac = AdaptiveConformal()
    cover = []
    for t in range(2000):
        y = 100 + rng.uniform(-10, 10); b = ac.band()
        if b is not None and t > 500: cover.append(abs(y - 100) <= b)
        ac.update(y, 100)
    cov = float(np.mean(cover)); assert 0.75 <= cov <= 0.85, cov
    assert abs(jain_index([1, 1, 1, 1]) - 1) < 1e-9 and jain_index([1, 0, 0, 0]) == 0.25
    rows, prev = [], "GENESIS"
    for i in range(3):
        body = {"id": i + 1, "at": f"t{i}", "actor": "op", "action": "approve", "payload": {"d": i}}
        h = ledger_hash(prev, body); rows.append({**body, "prev_hash": prev, "hash": h}); prev = h
    assert ledger_verify(rows)["ok"]
    rows[1]["payload"] = {"d": 99}
    assert ledger_verify(rows) == {"ok": False, "broken_at_index": 1, "id": 2}
    assert abs(burn_rate(0.0144, 0.999) - 14.4) < 1e-6
    print("analytics blueprints: all self-tests passed; conformal coverage=%.3f" % cov)
