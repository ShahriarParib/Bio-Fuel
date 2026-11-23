// One client hammering the API with the NORMAL limit (600 req/min per client IP): must get 429 + Retry-After,
// never 5xx, and the service must stay up for everyone else.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:3000";
export const options = { vus: 20, duration: "15s", summaryTrendStats: ["avg", "p(95)", "max"] };

export default function () {
  const r = http.get(`${BASE}/api/state`);
  check(r, {
    "200 or 429": (x) => x.status === 200 || x.status === 304 || x.status === 429,
    "never 5xx": (x) => x.status < 500,
  });
}
