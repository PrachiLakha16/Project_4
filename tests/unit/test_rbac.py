"""Tests for api.rbac: JWT decoding and role-based access checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from sre_agent.api.rbac import Principal, decode_token, get_current_principal, require_role
from sre_agent.core.config import Settings

_SECRET = "test-only-secret-at-least-32-bytes-long"
_AUDIENCE = "sre-incident-agent-test"


def _settings() -> Settings:
    return Settings(api_jwt_secret=_SECRET, api_jwt_audience=_AUDIENCE)


def _make_token(*, roles: list[str], sub: str = "alice", exp_delta: timedelta | None = None) -> str:
    payload = {"sub": sub, "roles": roles, "aud": _AUDIENCE}
    if exp_delta is not None:
        payload["exp"] = datetime.now(UTC) + exp_delta
    return jwt.encode(payload, _SECRET, algorithm="HS256")


def test_decode_token_valid_returns_principal_with_roles():
    token = _make_token(roles=["sre-oncall"])
    principal = decode_token(token, _settings())
    assert isinstance(principal, Principal)
    assert principal.subject == "alice"
    assert "sre-oncall" in principal.roles


def test_decode_token_invalid_signature_raises_401():
    token = jwt.encode(
        {"sub": "alice", "roles": [], "aud": _AUDIENCE},
        "wrong-secret-also-at-least-32-bytes",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc_info:
        decode_token(token, _settings())
    assert exc_info.value.status_code == 401


def test_decode_token_garbage_string_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        decode_token("this-is-not-a-jwt", _settings())
    assert exc_info.value.status_code == 401


def test_decode_token_expired_raises_401():
    token = _make_token(roles=["sre-oncall"], exp_delta=timedelta(minutes=-5))
    with pytest.raises(HTTPException) as exc_info:
        decode_token(token, _settings())
    assert exc_info.value.status_code == 401


def test_decode_token_wrong_audience_raises_401():
    token = jwt.encode(
        {"sub": "alice", "roles": [], "aud": "some-other-audience"}, _SECRET, algorithm="HS256"
    )
    with pytest.raises(HTTPException) as exc_info:
        decode_token(token, _settings())
    assert exc_info.value.status_code == 401


def test_decode_token_missing_roles_claim_defaults_to_empty_list():
    token = jwt.encode({"sub": "bob", "aud": _AUDIENCE}, _SECRET, algorithm="HS256")
    principal = decode_token(token, _settings())
    assert principal.roles == []


def test_get_current_principal_missing_credentials_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        get_current_principal(credentials=None, settings=_settings())
    assert exc_info.value.status_code == 401


def test_get_current_principal_valid_bearer_token():
    token = _make_token(roles=["sre-oncall"])
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    principal = get_current_principal(credentials=creds, settings=_settings())
    assert principal.subject == "alice"


def test_require_role_allows_matching_role():
    # require_role returns a FastAPI dependency function; call it directly
    # with an explicit `principal` kwarg (bypassing FastAPI's Depends()
    # resolution machinery, which is exercised at the HTTP layer, not here).
    dependency = require_role("sre-oncall")
    principal = Principal(subject="alice", roles=["sre-oncall", "viewer"])
    assert dependency(principal=principal) == principal


def test_require_role_rejects_missing_role():
    dependency = require_role("sre-oncall")
    principal = Principal(subject="eve", roles=["viewer"])
    with pytest.raises(HTTPException) as exc_info:
        dependency(principal=principal)
    assert exc_info.value.status_code == 403
