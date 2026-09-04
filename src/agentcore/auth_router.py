"""Authentication router (/api/auth/*).

Single-user opt-in auth driven by ``AGENTCORE_AUTH_ENABLED``:

Endpoints
---------
* ``GET  /api/auth/status``    — auth enabled? account registered? (public)
* ``POST /api/auth/register``  — create the single account (rate-limited)
* ``POST /api/auth/login``     — exchange credentials for a bearer token
                                 (rate-limited)
* ``POST /api/auth/logout``    — revoke the presented token

Registration returns 409 once the account exists; a forgotten password
is recovered by deleting ``auth.json`` and restarting (single-user
design, no email/password-reset machinery).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from agentcore import auth as auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class CredentialsBody(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=8, max_length=128)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _check_rate_limit(action: str, request: Request) -> None:
    """429 with Retry-After when the per-IP attempt budget is exhausted."""
    retry_after = auth_service.access_limiter.retry_after(
        action, _client_ip(request)
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts, please retry later",
            headers={"Retry-After": str(retry_after)},
        )


@router.get("/status")
def auth_status() -> dict[str, bool]:
    """Public probe the frontend uses to decide login vs register vs skip."""
    return {
        "enabled": auth_service.is_auth_enabled(),
        "registered": auth_service.has_registered_user(),
    }


@router.post("/register")
def register(body: CredentialsBody, request: Request) -> dict[str, str]:
    """Create the single account and immediately sign the caller in."""
    if not auth_service.is_auth_enabled():
        raise HTTPException(
            status_code=400, detail="Authentication is not enabled"
        )
    ip = _client_ip(request)
    _check_rate_limit("register", request)
    if not auth_service.register_user(body.username, body.password):
        auth_service.access_limiter.record_attempt("register", ip)
        raise HTTPException(status_code=409, detail="Account already exists")
    token = auth_service.create_token(body.username)
    return {"token": token, "username": body.username}


@router.post("/login")
def login(body: CredentialsBody, request: Request) -> dict[str, str]:
    ip = _client_ip(request)
    _check_rate_limit("login", request)
    if not auth_service.verify_credentials(body.username, body.password):
        auth_service.access_limiter.record_attempt("login", ip)
        # Generic message — never reveal which half was wrong.
        raise HTTPException(status_code=401, detail="Invalid credentials")
    auth_service.access_limiter.clear("login", ip)
    token = auth_service.create_token(body.username)
    return {"token": token, "username": body.username}


@router.post("/logout")
def logout(authorization: str | None = Header(default=None)) -> dict[str, bool]:
    """Revoke the presented token (its jti joins the revocation list)."""
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token or auth_service.verify_token(token) is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    jti, exp = auth_service.extract_jti(token)
    if jti:
        auth_service.revoke_token(jti, exp)
    return {"ok": True}
