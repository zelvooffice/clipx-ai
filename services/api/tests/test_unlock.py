"""Per-visit dashboard password: every tab must unlock, for every user."""

from __future__ import annotations

import json

import pytest

from app.config import Settings, get_settings
from app.security.password import hash_password, verify_password
from tests.conftest import signed_headers

PASSWORD = "correct horse battery staple"


@pytest.fixture
def gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", hash_password(PASSWORD))
    get_settings.cache_clear()


def _unlock(raw_client, password: str = PASSWORD, email: str = "owner@example.com"):  # type: ignore[no-untyped-def]
    body = json.dumps({"password": password}).encode()
    h = signed_headers("POST", "/v1/session/unlock", body, email)
    return raw_client.post("/v1/session/unlock", content=body, headers={**h, "content-type": "application/json"})


def _get(raw_client, path: str, token: str | None = None, email: str = "owner@example.com"):  # type: ignore[no-untyped-def]
    h = signed_headers("GET", path, email=email)
    if token:
        h["x-clipx-unlock"] = token
    return raw_client.get(path, headers=h)


def test_password_hashing() -> None:
    enc = hash_password(PASSWORD)
    assert PASSWORD not in enc and enc.startswith("scrypt:") and "$" not in enc
    assert verify_password(PASSWORD, enc) and not verify_password("wrong password!!", enc)
    with pytest.raises(ValueError):
        hash_password("short")


def test_everything_is_locked_until_unlocked(gated, raw_client) -> None:  # type: ignore[no-untyped-def]
    for path in ("/v1/overview", "/v1/clips", "/v1/settings", "/v1/publishing/queue"):
        r = _get(raw_client, path)
        assert r.status_code == 401 and r.json()["detail"] == "locked"
    status = _get(raw_client, "/v1/session").json()
    assert status["password_required"] is True and status["unlocked"] is False


def test_unlock_then_access_then_lock(gated, raw_client) -> None:  # type: ignore[no-untyped-def]
    r = _unlock(raw_client)
    assert r.status_code == 200
    token = r.json()["token"]
    assert _get(raw_client, "/v1/overview", token).status_code == 200
    assert _get(raw_client, "/v1/session", token).json()["unlocked"] is True
    h = signed_headers("POST", "/v1/session/lock", b"")
    raw_client.post("/v1/session/lock", headers={**h, "x-clipx-unlock": token})
    assert _get(raw_client, "/v1/overview", token).status_code == 401  # revoked immediately


def test_wrong_password_and_lockout(gated, raw_client) -> None:  # type: ignore[no-untyped-def]
    for left in (4, 3, 2, 1, 0):
        r = _unlock(raw_client, "not the password")
        assert r.status_code == 401 and f"{left} attempt" in r.json()["detail"]
    r = _unlock(raw_client)  # even the right password is refused during lockout
    assert r.status_code == 429


def test_token_is_bound_to_user_and_untamperable(gated, raw_client) -> None:  # type: ignore[no-untyped-def]
    token = _unlock(raw_client).json()["token"]
    parts = token.split(".")
    parts[2] = str(int(parts[2]) + 999999)  # extend expiry without the key
    assert _get(raw_client, "/v1/overview", ".".join(parts)).status_code == 401
    assert _get(raw_client, "/v1/overview", token + "x").status_code == 401
    assert _get(raw_client, "/v1/overview", "garbage").status_code == 401


def test_oauth_callback_not_blocked_by_lock(gated, raw_client) -> None:  # type: ignore[no-untyped-def]
    body = b'{"code": "abcd", "state": "xxxxxxxxxxxxxxxxxxxx"}'
    h = signed_headers("POST", "/v1/integrations/twitch/callback", body)
    r = raw_client.post("/v1/integrations/twitch/callback", content=body, headers={**h, "content-type": "application/json"})
    assert r.status_code == 403 and r.json()["detail"] != "locked"  # reaches the state check (unknown state)


def test_production_requires_password() -> None:
    s = Settings(environment="production", internal_api_secret="x" * 40, token_encryption_keys="k",
                 authorized_emails="a@b.com", storage_backend="r2", dashboard_password_hash="")
    assert any("DASHBOARD_PASSWORD_HASH" in p for p in s.production_problems())
