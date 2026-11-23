// Traffic surge (brief §11 "API traffic increases significantly"): 10 → SPIKE VUs in 5 s, hold, drop.
import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:3000";
const SPIKE = Number(__ENV.SPIKE || 300);
export const options = {
  stages: [
    { duration: "10s", target: 10 },
    { duration: "5s", target: SPIKE },
    { duration: "30s", target: SPIKE },
    { duration: "5s", target: 10 },
    { duration: "10s", target: 10 },
  ],
  summaryTrendStats: ["avg", "p(50)", "p(95)", "p(99)", "max"],
};

export default function () {
  const r = http.get(`${BASE}/api/state`, { tags: { name: "state" } });
  check(r, { "not 5xx": (x) => x.status < 500 });
  sleep(0.5);
}
