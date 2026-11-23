// Operator consoles polling the API through nginx, like the real UI does (state every 1 s, the rest less often).
// VUS and DURATION come from the environment: run_loadtest.sh steps VUS up to find the knee.
import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:3000";
export const options = {
  vus: Number(__ENV.VUS || 50),
  duration: __ENV.DURATION || "20s",
  summaryTrendStats: ["avg", "p(50)", "p(95)", "p(99)", "max"],
  thresholds: { http_req_failed: ["rate<0.01"] },
};

let etag = "";
export default function () {
  // ETag/304 like a browser: an unchanged tick costs no body
  const r = http.get(`${BASE}/api/state`, { headers: etag ? { "If-None-Match": etag } : {}, tags: { name: "state" } });
  if (r.status === 200) etag = r.headers["Etag"] || r.headers["ETag"] || "";
  check(r, { "state 200/304": (x) => x.status === 200 || x.status === 304 });
  if (__ITER % 3 === 0) {
    const rec = http.get(`${BASE}/api/recommendations`, { tags: { name: "recommendations" } });
    check(rec, { "recommendations 200": (x) => x.status === 200 });
    const al = http.get(`${BASE}/api/alerts`, { tags: { name: "alerts" } });
    check(al, { "alerts 200": (x) => x.status === 200 });
  }
  if (__ITER % 5 === 0) {
    http.get(`${BASE}/api/system/status`, { tags: { name: "system_status" } });
  }
  sleep(1);
}
