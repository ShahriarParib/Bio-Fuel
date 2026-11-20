"""Typed exceptions and response error parser for simulator interactions."""
from __future__ import annotations

from typing import Any

import httpx


class SimError(Exception):
    """Base class for all simulator errors."""


class SimUnavailable(SimError):
    """Network connection error, timeout, or 5xx without a simulator envelope."""


class SimFaultInjected(SimError):
    """Simulator returned an explicitly injected fault (503 FAULT_INJECTED)."""

    def __init__(self, message: str = "") -> None:
        super().__init__(f"FAULT_INJECTED: {message}")
        self.message = message


class SimDomainError(SimError):
    """Domain error from simulator (e.g. 409 IDEMPOTENCY_KEY_MISMATCH, ROUTE_DISRUPTED)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class SimValidationError(SimError):
    """Pydantic validation error returned by the simulator (422)."""

    def __init__(self, details: list[Any]) -> None:
        super().__init__(f"Validation error: {details}")
        self.details = details


class SimInvalidPayload(SimError):
    """Simulator returned a payload that failed our internal schema validation or cross-checks."""

    def __init__(self, resource: str, errors: list[Any], raw: Any = None) -> None:
        super().__init__(f"Invalid payload for '{resource}': {errors}")
        self.resource = resource
        self.errors = errors
        self.raw = raw


def parse_error(resp: httpx.Response) -> SimError:
    """Parse HTTP response into a typed SimError."""
    try:
        body = resp.json()
    except Exception:
        return SimUnavailable(f"HTTP {resp.status_code}: non-JSON response ({resp.text[:200]})")

    if isinstance(body, dict):
        # Organizer fault envelope: {"error": {"code": "FAULT_INJECTED", "message": "..."}}
        if isinstance(body.get("error"), dict):
            e = body["error"]
            if e.get("code") == "FAULT_INJECTED":
                return SimFaultInjected(e.get("message", ""))
            return SimUnavailable(f"{resp.status_code} error: {e}")

        # Domain error envelope: {"detail": {"code": "...", "message": "..."}}
        det = body.get("detail")
        if isinstance(det, dict):
            if det.get("code") == "FAULT_INJECTED":
                return SimFaultInjected(det.get("message", "stream"))
            return SimDomainError(
                resp.status_code,
                det.get("code", "UNKNOWN"),
                det.get("message", ""),
            )
        # Validation error envelope: {"detail": [{...}, {...}]}
        if isinstance(det, list):
            return SimValidationError(det)

    return SimUnavailable(f"HTTP {resp.status_code}: {str(body)[:200]}")
