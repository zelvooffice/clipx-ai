from __future__ import annotations

import time

import jwt
import pytest
import respx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Response

from app.security import crypto
from tests.conftest import signed_headers


# ------------------------------------------------------------------------------ token encryption
def test_encrypt_roundtrip_and_aad_binding() -> None:
    ct = crypto.encrypt("secret-token", associated_data="twitch:1")
    assert "secret-token" not in ct
    assert crypto.decrypt(ct, associated_data="twitch:1") == "secret-token"
    with pytest.raises(InvalidTag):
        crypto.decrypt(ct, associated_data="twitch:2")  # ciphertext bound to its owner


def test_tampered_ciphertext_rejected() -> None:
    ct = crypto.encrypt("x", associated_data="a")
    parts = ct.split(".")
    parts[3] = parts[3][:-4] + ("AAAA" if not parts[3].endswith("AAAA") else "BBBB")
    with pytest.raises(InvalidTag):
        crypto.decrypt(".".join(parts), associated_data="a")


def test_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    old = crypto.encrypt("v", associated_data="a")
    new_key = crypto.generate_key()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEYS", f"{new_key},{get_settings().token_encryption_keys}")
    get_settings.cache_clear()
    assert crypto.decrypt(old, associated_data="a") == "v"  # old key still decrypts
    fresh = crypto.encrypt("v2", associated_data="a")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEYS", new_key)
    get_settings.cache_clear()
    assert crypto.decrypt(fresh, associated_data="a") == "v2"
    with pytest.raises(crypto.TokenCryptoError):
        crypto.decrypt(old, associated_data="a")  # rotated out


# ------------------------------------------------------------------------------ request authentication
def test_no_credentials_is_401(raw_client) -> None:  # type: ignore[no-untyped-def]
    assert raw_client.get("/v1/me").status_code == 401


def test_valid_signature_allows(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/v1/me")
    assert r.status_code == 200
    assert r.json() == {"email": "owner@example.com", "via": "worker-signature"}


def test_bad_signature_401(raw_client) -> None:  # type: ignore[no-untyped-def]
    h = signed_headers("GET", "/v1/me", secret="wrong-secret-wrong-secret-wrong-secret")
    assert raw_client.get("/v1/me", headers=h).status_code == 401


def test_signature_bound_to_path_and_body(raw_client) -> None:  # type: ignore[no-untyped-def]
    h = signed_headers("GET", "/v1/overview")
    assert raw_client.get("/v1/me", headers=h).status_code == 401
    h = signed_headers("PUT", "/v1/settings/processing", b'{"min_quality":0.5}')
    r = raw_client.put("/v1/settings/processing", content=b'{"min_quality":0.1}',
                       headers={**h, "content-type": "application/json"})
    assert r.status_code == 401


def test_expired_signature_401(raw_client) -> None:  # type: ignore[no-untyped-def]
    h = signed_headers("GET", "/v1/me", ts=int(time.time()) - 3600)
    assert raw_client.get("/v1/me", headers=h).status_code == 401


def test_replay_rejected(raw_client) -> None:  # type: ignore[no-untyped-def]
    h = signed_headers("GET", "/v1/me")
    assert raw_client.get("/v1/me", headers=h).status_code == 200
    assert raw_client.get("/v1/me", headers=h).status_code == 401


def test_unlisted_email_forbidden(raw_client) -> None:  # type: ignore[no-untyped-def]
    h = signed_headers("GET", "/v1/me", email="stranger@example.com")
    assert raw_client.get("/v1/me", headers=h).status_code == 403


def test_managed_allowlist(client, raw_client) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/v1/settings/authorized-emails", {"email": "editor@example.com"}).status_code == 200
    assert raw_client.get("/v1/me", headers=signed_headers("GET", "/v1/me", email="editor@example.com")).status_code == 200
    emails = client.get("/v1/settings/authorized-emails").json()["managed"]
    client.delete(f"/v1/settings/authorized-emails/{emails[0]['id']}")
    assert raw_client.get("/v1/me", headers=signed_headers("GET", "/v1/me", email="editor@example.com")).status_code == 403


def test_rate_limit(client, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings

    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    codes = [client.get("/v1/me").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[-1] == 429


def test_webhook_endpoint_does_not_accept_unsigned(raw_client) -> None:  # type: ignore[no-untyped-def]
    assert raw_client.post("/webhooks/twitch/eventsub", content=b"{}").status_code == 403


def test_media_path_traversal_blocked(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/v1/media/../../etc/passwd").status_code in (400, 404)


def test_security_headers(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/v1/me")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_production_refuses_insecure_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    s = Settings(environment="production", internal_api_secret="short", authorized_emails="",
                 storage_backend="local")
    assert len(s.production_problems()) >= 3


# ------------------------------------------------------------------------------ Cloudflare Access JWT
@respx.mock
def test_access_jwt_verification(raw_client, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.security import auth

    team = "https://clipx-test.cloudflareaccess.com"
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", team)
    monkeypatch.setenv("CF_ACCESS_AUD", "aud-tag-123")
    get_settings.cache_clear()
    auth._JWKS_CACHE.clear()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": "k1", "alg": "RS256", "use": "sig"})
    respx.get(f"{team}/cdn-cgi/access/certs").mock(return_value=Response(200, json={"keys": [jwk]}))

    def token(**over):  # type: ignore[no-untyped-def]
        claims = {"email": "owner@example.com", "aud": ["aud-tag-123"], "iss": team,
                  "iat": int(time.time()), "exp": int(time.time()) + 300, **over}
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})

    assert raw_client.get("/v1/me", headers={"cf-access-jwt-assertion": token()}).json()["via"] == "access-jwt"
    assert raw_client.get("/v1/me", headers={"cf-access-jwt-assertion": token(aud=["other"])}).status_code == 401
    assert raw_client.get("/v1/me", headers={"cf-access-jwt-assertion": token(iss="https://evil")}).status_code == 401
    assert raw_client.get("/v1/me", headers={"cf-access-jwt-assertion": token(exp=1)}).status_code == 401
    assert raw_client.get("/v1/me", headers={"cf-access-jwt-assertion": token(email="x@evil.com")}).status_code == 403
