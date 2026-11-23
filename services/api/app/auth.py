"""Operator authentication (hackathon scope: static bearer tokens from the environment, never from code).

Two roles can act: operator (approve/reject, mode, plan-now) and supervisor (everything an operator can, plus policy).
Read endpoints stay open so judges can look around without credentials.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from .config import ApiSettings, get_settings


@dataclass(frozen=True)
class Actor:
    name: str
    role: str  # operator | supervisor


def _match(token: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(token.encode(), expected.encode())


async def require_operator(
    authorization: str | None = Header(default=None),
    settings: ApiSettings = Depends(get_settings),
) -> Actor:
    """Validate the Bearer token; returns who is acting (used for the audit log)."""
    if not authorization:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Missing Authorization header"})
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail={"code": "BAD_AUTH_SCHEME", "message": "Expected Bearer token"})
    token = parts[1]
    if _match(token, settings.api_supervisor_token):
        return Actor("supervisor", "supervisor")
    if _match(token, settings.api_operator_token):
        return Actor("operator", "operator")
    raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Invalid operator credentials"})


async def require_supervisor(actor: Actor = Depends(require_operator)) -> Actor:
    if actor.role != "supervisor":
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Supervisor role required"})
    return actor
