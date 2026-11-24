"""A deliberately small, deliberately distrustful LLM client.

The platform's decisions are computed by the optimiser and the constraint checks in `services/engine`. The model
in here never participates in them. Its only job is to re-word facts we already computed so an operator who is
not an engineer can read them, which is why everything below is about *limiting* it:

* it is optional - with no API key the caller gets `LlmUnavailable` and falls back to a written template;
* it has a hard timeout, because an operator waiting on a crisis page cannot wait on a third party;
* consecutive failures open a cooldown, so a dead provider is asked once a minute, not once a click;
* the caller passes the exact numbers the answer may contain, and `check_grounded()` rejects any output that
  invented a different one. A fluent sentence with the wrong litre count is worse than no sentence.

Provider: Google Gemini's generateContent REST API. The key is read from the environment only.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

import httpx
from prometheus_client import Counter, Histogram

from .logging import get_logger

log = get_logger()

LLM_REQUESTS = Counter(
    "fsip_llm_requests_total",
    "Calls to the language model by outcome",
    # ok | timeout | overloaded | auth_error | truncated | http_error | rejected_ungrounded | disabled | cooldown
    ["outcome"],
)
LLM_LATENCY = Histogram(
    "fsip_llm_latency_seconds",
    "Language model round-trip latency",
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8),
)
LLM_CACHE = Counter(
    "fsip_llm_cache_total",
    "Explanation cache lookups",
    ["result"],    # hit | miss
)

# A pinned model so results are reproducible, then a floating alias as the spare. Hosted models get retired and
# they return 503 under load (we measured both), so a single model name is a single point of failure for a feature
# we demo live. The spare costs one extra call only when the first one is actually unavailable.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_SPARE = "gemini-flash-lite-latest"
_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_RETRYABLE = {404, 408, 429, 500, 502, 503, 504}   # model retired, rate limited, or overloaded: try the spare


class LlmUnavailable(RuntimeError):
    """Raised whenever the caller must fall back to the template. Never surfaced to an operator as an error."""

    retryable: bool = False


def _unavailable(message: str, *, retryable: bool = False) -> LlmUnavailable:
    err = LlmUnavailable(message)
    err.retryable = retryable
    return err


@dataclass
class LlmResult:
    text: str
    source: str          # "gemini" | "template"
    model: str = ""
    latency_ms: int = 0
    cached: bool = False
    note: str = ""       # why the fallback happened, shown in the UI when source == "template"


@dataclass
class GeminiClient:
    api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    spare_model: str = field(default_factory=lambda: os.getenv("GEMINI_SPARE_MODEL", DEFAULT_SPARE))
    timeout_s: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SEC", "4")))
    budget_s: float = field(default_factory=lambda: float(os.getenv("LLM_BUDGET_SEC", "6")))
    # measured: a real decision's facts produce ~150 output tokens, and a 220 cap cut answers mid-sentence.
    # Only tokens actually generated are billed, so a generous cap costs nothing and removes a failure mode.
    max_output_tokens: int = 600
    failure_threshold: int = 3
    cooldown_s: float = 60.0

    _failures: int = field(default=0, init=False)
    _blocked_until: float = field(default=0.0, init=False)
    _last_model: str = field(default="", init=False)

    @property
    def models(self) -> list[str]:
        """The primary, then the spare if it is a different name."""
        return [self.model] + ([self.spare_model] if self.spare_model and self.spare_model != self.model else [])

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def in_cooldown(self) -> bool:
        return time.monotonic() < self._blocked_until

    def status(self) -> dict[str, object]:
        """What the operator console shows about the assistant itself."""
        if not self.enabled:
            return {"state": "disabled", "model": "", "models": [],
                    "detail": "no API key configured; explanations use the platform's written summaries"}
        if self.in_cooldown:
            return {"state": "cooling_down", "model": self.model, "models": self.models,
                    "detail": f"{self._failures} failed attempts in a row; retrying in "
                              f"{max(0, int(self._blocked_until - time.monotonic()))}s"}
        return {"state": "ready", "model": self.model, "models": self.models,
                "detail": f"answered last by {self._last_model}" if self._last_model else ""}

    async def complete(self, instruction: str, facts: str) -> tuple[str, float]:
        """Return (text, latency_seconds) or raise LlmUnavailable. Never raises anything else.

        Tries the primary model and, if it answers with a retryable status inside the time budget, the spare.
        """
        if not self.enabled:
            LLM_REQUESTS.labels("disabled").inc()
            raise LlmUnavailable("no API key configured")
        if self.in_cooldown:
            LLM_REQUESTS.labels("cooldown").inc()
            raise LlmUnavailable("provider in cooldown after repeated failures")

        started = time.monotonic()
        last: LlmUnavailable | None = None
        for i, model in enumerate(self.models):
            if i and time.monotonic() - started > self.budget_s:
                break                       # an operator is waiting; do not keep trying models
            try:
                text, elapsed = await self._attempt(model, instruction, facts)
            except LlmUnavailable as err:
                last = err
                if not getattr(err, "retryable", False):
                    break
                continue
            self._failures = 0
            self._last_model = model
            LLM_REQUESTS.labels("ok").inc()
            return text, time.monotonic() - started
        self._record_failure()
        raise last or LlmUnavailable("no model answered")

    async def _attempt(self, model: str, instruction: str, facts: str) -> tuple[str, float]:
        payload = {
            "systemInstruction": {"parts": [{"text": instruction}]},
            "contents": [{"role": "user", "parts": [{"text": facts}]}],
            "generationConfig": {
                "temperature": 0.2,          # we want the same facts worded the same way, not variety
                "maxOutputTokens": self.max_output_tokens,
                "candidateCount": 1,
            },
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(
                    f"{_BASE}/{model}:generateContent",
                    headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
                    json=payload,
                )
        except httpx.TimeoutException as err:
            LLM_LATENCY.observe(time.monotonic() - started)
            LLM_REQUESTS.labels("timeout").inc()
            log.warning("llm.timeout", model=model, timeout_s=self.timeout_s)
            raise _unavailable(f"{model} timed out after {self.timeout_s}s", retryable=True) from err
        except Exception as err:                       # DNS, TLS, connection reset
            LLM_REQUESTS.labels("http_error").inc()
            # an API key can appear in error text for some transport failures: log the type only
            log.warning("llm.transport_failed", error_type=type(err).__name__, model=model)
            raise _unavailable(f"provider unreachable ({type(err).__name__})", retryable=True) from err

        elapsed = time.monotonic() - started
        LLM_LATENCY.observe(elapsed)

        if resp.status_code >= 400:
            retryable = resp.status_code in _RETRYABLE
            outcome = ("overloaded" if resp.status_code in (429, 503)
                       else "auth_error" if resp.status_code in (401, 403) else "http_error")
            LLM_REQUESTS.labels(outcome).inc()
            log.warning("llm.http_error", model=model, status=resp.status_code, outcome=outcome)
            raise _unavailable(f"{model}: {outcome} (HTTP {resp.status_code})", retryable=retryable)

        try:
            text, finish = _first_text(resp.json())
        except Exception as err:
            LLM_REQUESTS.labels("http_error").inc()
            raise _unavailable("unreadable response from the provider", retryable=True) from err
        if not text.strip():
            LLM_REQUESTS.labels("http_error").inc()
            raise _unavailable(f"{model} returned an empty answer", retryable=True)
        if finish and finish != "STOP":
            # a sentence that stops halfway is worse than no sentence: an operator would act on half a fact
            LLM_REQUESTS.labels("truncated").inc()
            log.warning("llm.truncated", model=model, finish_reason=finish)
            raise _unavailable(f"{model} stopped early ({finish.lower()})", retryable=True)
        return text.strip(), elapsed

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._blocked_until = time.monotonic() + self.cooldown_s
            log.warning("llm.cooldown_opened", failures=self._failures, seconds=self.cooldown_s)


def _first_text(body: dict) -> tuple[str, str]:
    """Return (text, finishReason). An empty finishReason means the provider did not say."""
    for cand in body.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            if part.get("text"):
                return str(part["text"]), str(cand.get("finishReason") or "")
    return "", str(((body.get("candidates") or [{}])[0]).get("finishReason") or "")


def normalise_number(raw: str) -> str:
    """'12,000' / '12000.0' / '12000' all compare equal; '0.87' and '87' do not."""
    s = raw.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def check_grounded(text: str, allowed: set[str]) -> str | None:
    """Return the first number in `text` that is not in `allowed`, or None when every number checks out.

    This is the guard that lets us put model output in front of an operator at all: the explanation may only
    repeat numbers the platform itself computed.
    """
    permitted = {normalise_number(a) for a in allowed}
    for found in _NUMBER.findall(text):
        if normalise_number(found) not in permitted:
            return found
    return None


def collect_numbers(value: object) -> set[str]:
    """Every number appearing anywhere in the facts we hand the model, as strings."""
    out: set[str] = set()
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, int | float):
        out.add(normalise_number(f"{value:.4f}" if isinstance(value, float) else str(value)))
        out.add(normalise_number(str(round(float(value)))))
        out.add(normalise_number(f"{float(value):.1f}"))
        return out
    if isinstance(value, str):
        out.update(normalise_number(n) for n in _NUMBER.findall(value))
        return out
    if isinstance(value, dict):
        for k, v in value.items():
            out |= collect_numbers(k) | collect_numbers(v)
        return out
    if isinstance(value, list | tuple | set):
        for v in value:
            out |= collect_numbers(v)
    return out
