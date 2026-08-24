"""RBAC dependency for FastAPI endpoints.

Verifies a JWT (HS256 locally; an OIDC-issued RS256 token behind the real
ALB/ingress in prod -- see SECURITY.md) and enforces role claims. The
`/approvals/{action_id}` endpoint requires the `sre-oncall` role so only
on-call engineers can approve/reject a pending remediation action.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sre_agent.core.config import Settings, get_settings

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    subject: str
    roles: list[str]


def decode_token(token: str, settings: Settings) -> Principal:
    try:
        payload = jwt.decode(
            token, settings.api_jwt_secret, algorithms=["HS256"], audience=settings.api_jwt_audience
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {exc}"
        ) from exc
    return Principal(subject=payload.get("sub", "unknown"), roles=payload.get("roles", []))


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    return decode_token(credentials.credentials, settings)


def require_role(role: str):
    def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if role not in principal.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"requires role '{role}'"
            )
        return principal

    return _dependency


require_oncall = require_role("sre-oncall")
