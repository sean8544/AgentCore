"""Tests for the single-user opt-in auth module and rate limiter."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentcore import auth as auth_service
from agentcore.api import app
from agentcore.auth import (
    RateLimitPolicy,
    SlidingWindowLimiter,
    create_token,
    extract_jti,
    hash_password,
    has_registered_user,
    register_user,
    revoke_token,
    verify_credentials,
    verify_password,
    verify_token,
)

FAST_ITERATIONS = 1_000  # keep PBKDF2 tests fast (security value unchanged)


@pytest.fixture
def fast_pbkdf2(monkeypatch, tmp_path):
    # Isolate persistence (auth.json) from the real project data dir.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auth_service, "_PASSWORD_ITERATIONS", FAST_ITERATIONS)


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client in an isolated data dir, auth enabled, fast hashing."""
    monkeypatch.setattr(auth_service, "_PASSWORD_ITERATIONS", FAST_ITERATIONS)
    monkeypatch.setenv("AGENTCORE_AUTH_ENABLED", "true")
    # Fresh limiter per test so attempt counters never leak across cases.
    monkeypatch.setattr(
        auth_service,
        "access_limiter",
        SlidingWindowLimiter(
            {
                "login": RateLimitPolicy(3, window_seconds=300, block_seconds=300),
                "register": RateLimitPolicy(3, window_seconds=3600, block_seconds=3600),
            }
        ),
    )
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_password_hash_and_verify(fast_pbkdf2) -> None:
    digest, salt = hash_password("s3cret-pass-1")
    assert verify_password("s3cret-pass-1", digest, salt)
    assert not verify_password("wrong-pass-1", digest, salt)


def test_password_hash_uses_unique_salts(fast_pbkdf2) -> None:
    digest_a, salt_a = hash_password("same-password")
    digest_b, salt_b = hash_password("same-password")
    assert salt_a != salt_b
    assert digest_a != digest_b


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def test_token_roundtrip(fast_pbkdf2) -> None:
    token = create_token("admin")
    assert verify_token(token) == "admin"


def test_token_rejects_tampering_and_garbage(fast_pbkdf2) -> None:
    token = create_token("admin")
    payload_b64, _, sig = token.partition(".")
    assert verify_token(f"{payload_b64}.{'0' * len(sig)}") is None
    assert verify_token("not-a-token") is None
    assert verify_token("") is None


def test_token_expiry(fast_pbkdf2) -> None:
    token = create_token("admin", expiry_seconds=-10)
    assert verify_token(token) is None


def test_token_revocation(fast_pbkdf2) -> None:
    token = create_token("admin")
    jti, exp = extract_jti(token)
    assert jti
    revoke_token(jti, exp)
    assert verify_token(token) is None


# ---------------------------------------------------------------------------
# Single-user account management
# ---------------------------------------------------------------------------


def test_register_single_user_only(fast_pbkdf2) -> None:
    assert not has_registered_user()
    assert register_user("admin", "password-123")
    assert has_registered_user()
    # Second registration attempt must fail (single-user design).
    assert not register_user("intruder", "password-456")
    assert verify_credentials("admin", "password-123")
    assert not verify_credentials("admin", "password-999")
    assert not verify_credentials("intruder", "password-456")


def test_fail_closed_on_corrupt_auth_file(fast_pbkdf2, tmp_path) -> None:
    (tmp_path / ".agentcore").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agentcore" / "auth.json").write_text("{corrupt", encoding="utf-8")
    # Corrupt file must not look like "no account" (that would reopen
    # registration) and must reject every token / credential check.
    assert not has_registered_user()
    assert not verify_credentials("admin", "password-123")
    token = "abc.def"
    assert verify_token(token) is None


# ---------------------------------------------------------------------------
# Sliding-window rate limiter
# ---------------------------------------------------------------------------


def test_limiter_allows_within_window_then_blocks() -> None:
    clock_value = [1000.0]
    limiter = SlidingWindowLimiter(
        {"login": RateLimitPolicy(3, window_seconds=60, block_seconds=120)},
        clock=lambda: clock_value[0],
    )
    for _ in range(3):
        assert limiter.retry_after("login", "1.2.3.4") is None
        limiter.record_attempt("login", "1.2.3.4")
    # 4th attempt — window full → blocked for block_seconds.
    assert limiter.retry_after("login", "1.2.3.4") == 120
    # Other IPs unaffected.
    assert limiter.retry_after("login", "5.6.7.8") is None
    # Block lifts after block_seconds; counters reset.
    clock_value[0] += 121
    assert limiter.retry_after("login", "1.2.3.4") is None


def test_limiter_window_slides_and_clear_resets() -> None:
    clock_value = [1000.0]
    limiter = SlidingWindowLimiter(
        {"login": RateLimitPolicy(2, window_seconds=60, block_seconds=60)},
        clock=lambda: clock_value[0],
    )
    limiter.record_attempt("login", "1.2.3.4")
    clock_value[0] += 50
    limiter.record_attempt("login", "1.2.3.4")
    assert limiter.retry_after("login", "1.2.3.4") == 60
    # Successful auth clears the counters.
    limiter.clear("login", "1.2.3.4")
    assert limiter.retry_after("login", "1.2.3.4") is None
    # Old attempts slide out of the window.
    limiter.record_attempt("login", "9.9.9.9")
    clock_value[0] += 61
    limiter.record_attempt("login", "9.9.9.9")
    assert limiter.retry_after("login", "9.9.9.9") is None


# ---------------------------------------------------------------------------
# HTTP integration (middleware + router)
# ---------------------------------------------------------------------------


def test_disabled_auth_is_passthrough(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENTCORE_AUTH_ENABLED", raising=False)
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        # Protected route reachable without a token while disabled.
        assert test_client.get("/api/agents").status_code == 200
        status = test_client.get("/api/auth/status").json()
        assert status["enabled"] is False
        # Registration refused while auth is off.
        resp = test_client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "password-123"},
        )
        assert resp.status_code == 400


def test_enabled_auth_blocks_api_without_token(client) -> None:
    assert client.get("/api/agents").status_code == 401
    assert client.get("/api/agents").json()["detail"] == "Not authenticated"
    # Public paths stay open so the login page can render.
    assert client.get("/health").status_code == 200
    assert client.get("/api/auth/status").status_code == 200


def test_register_login_logout_flow(client) -> None:
    status = client.get("/api/auth/status").json()
    assert status == {"enabled": True, "registered": False}

    # Register the single account → immediately signed in.
    resp = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "password-123"},
    )
    assert resp.status_code == 200
    token = resp.json()["token"]
    assert client.get(
        "/api/agents", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200

    # Second registration → 409.
    resp = client.post(
        "/api/auth/register",
        json={"username": "admin2", "password": "password-456"},
    )
    assert resp.status_code == 409
    assert client.get("/api/auth/status").json()["registered"] is True

    # Login with correct credentials works; wrong ones do not.
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "password-123"},
    )
    assert resp.status_code == 200
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-pass-99"},
    )
    assert resp.status_code == 401

    # Logout revokes the token.
    assert (
        client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 200
    )
    assert (
        client.get("/api/agents", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )


def test_login_rate_limit_returns_429(client) -> None:
    client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "password-123"},
    )
    # Test limiter allows 3 login attempts per window.
    for _ in range(3):
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-pass-99"},
        )
        assert resp.status_code == 401
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-pass-99"},
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    # Even correct credentials stay blocked while the lockout is active.
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "password-123"},
    )
    assert resp.status_code == 429
