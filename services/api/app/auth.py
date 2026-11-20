"""Authentication and role validation dependencies."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from .config import ApiSettings, get_settings


async def require_operator(
    authorization: str | None = Header(default=None),
    settings: ApiSettings = Depends(get_settings),
) -> str:
    """Validate Bearer token for operator actions."""
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Missing Authorization header"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"code": "BAD_AUTH_SCHEME", "message": "Expected Bearer token"},
        )

    token = parts[1]
    # Accept configured operator token or demo operator token
    if token in (settings.api_operator_token, "operator", "supervisor"):
        return token

    raise HTTPException(
        status_code=403,
        detail={"code": "FORBIDDEN", "message": "Invalid operator credentials"},
    )
