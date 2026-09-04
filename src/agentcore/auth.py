"""Single-user opt-in authentication for the AgentCore control plane.

Authentication is disabled by default and only enabled when the
environment variable ``AGENTCORE_AUTH_ENABLED`` is set to a truthy
value (``true``, ``1``, ``yes``).  Credentials are created through a
web-based registration flow rather than environment variables, so that
agents running inside the process cannot read plaintext passwords.

Single-user design: only one account can be registered.  If the user
forgets their password, delete ``auth.json`` from the data directory
and restart the service to re-register.

Uses only Python stdlib (hashlib, hmac, secrets) to avoid adding new
dependencies:

* Passwords — PBKDF2-HMAC-SHA256 (600k iterations, OWASP 2023+).
* Tokens    — ``base64(payload).HMAC-SHA256`` bearer tokens carrying a
  ``jti`` id, revocable via a persisted revocation list.

Also hosts the sliding-window login/register rate limiter and the
``AuthMiddleware`` that protects every ``/api/*`` endpoint except the
public auth endpoints.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

from starlette.types import ASGIApp, Receive, Scope, Send

from agentcore.runtime import paths

logger = logging.getLogger(__name__)

# Token validity: 7 days (default)
TOKEN_EXPIRY_SECONDS = 7 * 24 * 3600

# PBKDF2 iteration count — OWASP recommended minimum for HMAC-SHA256.
_PASSWORD_ITERATIONS = 600_000

_TRUTHY = {"1", "true", "yes", "on"}

# /api/* paths reachable without a token (the auth flow itself).
_PUBLIC_API_PATHS: frozenset[str] = frozenset(
    {
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/status",
    }
)


def is_auth_enabled() -> bool:
    """Whether ``AGENTCORE_AUTH_ENABLED`` is set to a truthy value."""
    return os.environ.get("AGENTCORE_AUTH_ENABLED", "").strip().lower() in _TRUTHY


def auth_file() -> "Any":
    """Path of the persisted credentials file (``auth.json``)."""
    return paths.get_data_dir() / "auth.json"


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256)
# ---------------------------------------------------------------------------


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Hash *password* → ``(hash_hex, salt_hex)``."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _PASSWORD_ITERATIONS,
    )
    return digest.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Constant-time comparison of *password* against the stored hash."""
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, stored_hash)


# ---------------------------------------------------------------------------
# Auth data persistence (auth.json, fail-closed)
# ---------------------------------------------------------------------------


def _load_auth_data() -> dict:
    """Load ``auth.json`` from the data directory.

    Returns a sentinel with ``_auth_load_error`` set when the file exists
    but cannot be read/parsed, so callers fail closed instead of silently
    bypassing auth (an empty dict would look like "no user registered").
    """
    path = auth_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"_auth_load_error": True}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load auth file %s: %s", path, exc)
        return {"_auth_load_error": True}


def _save_auth_data(data: dict) -> None:
    """Atomically save ``auth.json`` with restrictive permissions."""
    path = auth_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Token generation / verification (HMAC-SHA256, no PyJWT needed)
# ---------------------------------------------------------------------------


def _get_token_secret() -> str:
    """Return the signing secret, creating one if absent."""
    data = _load_auth_data()
    if data.get("_auth_load_error"):
        return ""
    secret = data.get("token_secret", "")
    if not secret:
        secret = secrets.token_hex(32)
        data["token_secret"] = secret
        _save_auth_data(data)
    return secret


def create_token(username: str, expiry_seconds: int = TOKEN_EXPIRY_SECONDS) -> str:
    """Create an HMAC-signed token: ``base64url(payload).signature``.

    The payload carries a unique ``jti`` so individual tokens can be
    revoked (logout) without invalidating every session.
    """
    secret = _get_token_secret()
    token_id = secrets.token_hex(16)
    payload = json.dumps(
        {
            "sub": username,
            "iat": int(time.time()),
            "exp": int(time.time()) + expiry_seconds,
            "jti": token_id,
        }
    )
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_token(token: str) -> Optional[str]:
    """Verify *token* → username, or ``None`` when invalid/expired/revoked."""
    secret = _get_token_secret()
    if not secret:
        return None
    try:
        payload_b64, _, sig = token.partition(".")
        if not sig:
            return None
        expected_sig = hmac.new(
            secret.encode(), payload_b64.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        jti = payload.get("jti")
        if jti and _is_token_revoked(jti):
            return None
        return payload.get("sub")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.debug("Token verification failed: %s", exc)
        return None


def extract_jti(token: str) -> tuple[Optional[str], int]:
    """Return ``(jti, exp)`` of a token WITHOUT signature verification.

    Only used by logout on an already-verified token.
    """
    try:
        payload_b64 = token.partition(".")[0]
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("jti"), int(payload.get("exp", 0))
    except (ValueError, json.JSONDecodeError):
        return None, 0


# ---------------------------------------------------------------------------
# Token revocation
# ---------------------------------------------------------------------------


def _is_token_revoked(jti: str) -> bool:
    data = _load_auth_data()
    if data.get("_auth_load_error"):
        return True  # fail closed
    return jti in data.get("revoked_tokens_meta", {})


def revoke_token(jti: str, exp: int) -> None:
    """Add a token id to the revocation list (O(1) dict lookup)."""
    data = _load_auth_data()
    if data.get("_auth_load_error"):
        return
    meta = data.setdefault("revoked_tokens_meta", {})
    # Drop entries that expired anyway — keeps the list bounded.
    now = int(time.time())
    for stale in [k for k, e in meta.items() if e < now]:
        meta.pop(stale, None)
    meta[jti] = exp
    _save_auth_data(data)


# ---------------------------------------------------------------------------
# Single-user account management
# ---------------------------------------------------------------------------


def has_registered_user() -> bool:
    """Whether the single account already exists."""
    data = _load_auth_data()
    return bool(data.get("username")) and not data.get("_auth_load_error")


def register_user(username: str, password: str) -> bool:
    """Create the single account. Returns False when one already exists."""
    data = _load_auth_data()
    if data.get("_auth_load_error") or data.get("username"):
        return False
    password_hash, salt = hash_password(password)
    data.update(
        {
            "username": username,
            "password_hash": password_hash,
            "password_salt": salt,
            "created_at": int(time.time()),
        }
    )
    _save_auth_data(data)
    logger.info("Registered control-plane user %r", username)
    return True


def verify_credentials(username: str, password: str) -> bool:
    """Check *username*/*password* against the stored account."""
    data = _load_auth_data()
    if data.get("_auth_load_error"):
        return False
    if username != data.get("username"):
        # Burn comparable time so wrong-username probes do not leak that
        # the account name exists via timing.
        hash_password(password)
        return False
    return verify_password(password, data.get("password_hash", ""), data.get("password_salt", ""))


# ---------------------------------------------------------------------------
# Sliding-window rate limiter (login / registration abuse protection)
# ---------------------------------------------------------------------------


class RateLimitPolicy:
    """Attempts allowed within a window before a temporary block."""

    def __init__(self, max_attempts: int, window_seconds: int, block_seconds: int):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds


class SlidingWindowLimiter:
    """In-memory per-(action, client-ip) limiter.

    Mirrors the qwenpaw Hub pattern: attempts inside the window are
    counted; exceeding ``max_attempts`` triggers a ``block_seconds``
    lockout.  Successful authentication clears the counter.
    """

    def __init__(
        self,
        policies: dict[str, RateLimitPolicy],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policies = policies
        self._clock = clock
        self._lock = threading.Lock()
        self._attempts: dict[tuple[str, str], deque[float]] = {}
        self._blocked_until: dict[tuple[str, str], float] = {}

    def retry_after(self, action: str, address: str) -> Optional[int]:
        """Seconds to wait before retrying, or ``None`` when allowed."""
        policy = self._policies.get(action)
        if policy is None:
            return None
        key = (action, address)
        now = self._clock()
        with self._lock:
            blocked_until = self._blocked_until.get(key, 0.0)
            if blocked_until > now:
                return max(1, math.ceil(blocked_until - now))
            if key in self._blocked_until:
                self._blocked_until.pop(key, None)
                self._attempts.pop(key, None)
                return None
            attempts = self._attempts.setdefault(key, deque())
            self._discard_expired(attempts, now, policy.window_seconds)
            if len(attempts) < policy.max_attempts:
                return None
            self._blocked_until[key] = now + policy.block_seconds
            return policy.block_seconds

    def record_attempt(self, action: str, address: str) -> None:
        """Record a failed authentication attempt."""
        policy = self._policies.get(action)
        if policy is None:
            return
        key = (action, address)
        now = self._clock()
        with self._lock:
            attempts = self._attempts.setdefault(key, deque())
            self._discard_expired(attempts, now, policy.window_seconds)
            attempts.append(now)

    def clear(self, action: str, address: str) -> None:
        """Clear counters after successful authentication."""
        key = (action, address)
        with self._lock:
            self._attempts.pop(key, None)
            self._blocked_until.pop(key, None)

    def _discard_expired(
        self, attempts: deque[float], now: float, window_seconds: int
    ) -> None:
        threshold = now - window_seconds
        while attempts and attempts[0] <= threshold:
            attempts.popleft()


#: Shared limiter instance — login: 10 tries / 5 min then 5 min lockout;
#: registration: 5 tries / hour then 1 hour lockout.
access_limiter = SlidingWindowLimiter(
    {
        "login": RateLimitPolicy(max_attempts=10, window_seconds=300, block_seconds=300),
        "register": RateLimitPolicy(max_attempts=5, window_seconds=3600, block_seconds=3600),
    }
)


# ---------------------------------------------------------------------------
# AuthMiddleware — pure ASGI, SSE-safe, fail-closed
# ---------------------------------------------------------------------------


def _extract_bearer(scope: Scope) -> str:
    """Pull the Bearer token out of the ASGI scope headers."""
    for key, value in scope.get("headers", []):
        if key.lower() == b"authorization":
            decoded = value.decode("latin-1")
            if decoded.lower().startswith("bearer "):
                return decoded[7:].strip()
    return ""


class AuthMiddleware:
    """Reject unauthenticated requests to ``/api/*`` when auth is enabled.

    Pure ASGI (no response buffering) so SSE streaming responses pass
    through untouched.  Only ``/api/*`` paths are protected — static SPA
    assets, ``/health`` and ``/navigation`` stay public so the login
    page itself can render.  When ``AGENTCORE_AUTH_ENABLED`` is falsy
    the middleware is a zero-overhead pass-through, keeping the default
    local development experience unchanged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not is_auth_enabled() or scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/api/") or path in _PUBLIC_API_PATHS:
            await self.app(scope, receive, send)
            return

        token = _extract_bearer(scope)
        if token and verify_token(token):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"detail":"Not authenticated"}',
            }
        )
