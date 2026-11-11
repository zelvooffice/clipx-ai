"""Twitch OAuth + Helix client.

Endpoints and semantics verified against https://dev.twitch.tv/docs/api/reference (2026-11-11):
* POST /helix/clips — ``broadcaster_id``, optional ``title`` and ``duration`` (5–60 s, default 30);
  captures up to 90 s ending at request time; returns 202 + ``id``/``edit_url``; confirm via Get Clips,
  assume failure if not returned within 60 s. 403 = clipping restricted/disabled, 404 = not live.
* POST /helix/videos/clips — Create Clip From VOD (``editor_id``, ``broadcaster_id``, ``vod_id``,
  ``vod_offset`` = clip END, ``duration``, ``title``); scopes editor:manage:clips | channel:manage:clips.
* GET /helix/clips/downloads — ``editor_id``, ``broadcaster_id``, 1–10 ``clip_id``; returns
  ``landscape_download_url`` / ``portrait_download_url`` (nullable). 100 req/min.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings

log = logging.getLogger("clipx.twitch")

HELIX = "https://api.twitch.tv/helix"
ID = "https://id.twitch.tv/oauth2"

SCOPES_BY_ROLE: dict[str, list[str]] = {
    # Any account: create native clips around "now" and read chat as an audience signal.
    "operator": ["clips:edit", "user:read:chat"],
    # An editor of the target channel: also precise VOD clips + official clip downloads.
    "editor": ["clips:edit", "user:read:chat", "editor:manage:clips"],
    # The broadcaster themselves.
    "broadcaster": ["clips:edit", "user:read:chat", "channel:manage:clips"],
}


class TwitchError(Exception):
    def __init__(self, status: int, message: str, payload: Any = None) -> None:
        super().__init__(f"Twitch {status}: {message}")
        self.status = status
        self.message = message
        self.payload = payload


class ClipRestricted(TwitchError):
    """403 from clip creation: clips disabled/restricted or user banned. Never retried around."""


class NotLive(TwitchError):
    """404 from Create Clip: the broadcaster is not live."""


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scopes: list[str]


# --------------------------------------------------------------------------------------- OAuth
def authorize_url(state: str, role: str) -> str:
    s = get_settings()
    params = {
        "client_id": s.twitch_client_id,
        "redirect_uri": s.twitch_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES_BY_ROLE[role]),
        "state": state,
        "force_verify": "true",
    }
    return f"{ID}/authorize?{urlencode(params)}"


async def exchange_code(code: str, client: httpx.AsyncClient | None = None) -> TokenSet:
    s = get_settings()
    data = {
        "client_id": s.twitch_client_id,
        "client_secret": s.twitch_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": s.twitch_redirect_uri,
    }
    return await _token_request(data, client)


async def refresh(refresh_token: str, client: httpx.AsyncClient | None = None) -> TokenSet:
    s = get_settings()
    data = {
        "client_id": s.twitch_client_id,
        "client_secret": s.twitch_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    return await _token_request(data, client)


async def app_token(client: httpx.AsyncClient | None = None) -> TokenSet:
    s = get_settings()
    data = {"client_id": s.twitch_client_id, "client_secret": s.twitch_client_secret,
            "grant_type": "client_credentials"}
    return await _token_request(data, client)


async def _token_request(data: dict[str, str], client: httpx.AsyncClient | None) -> TokenSet:
    async with _client(client) as c:
        r = await c.post(f"{ID}/token", data=data)
    if r.status_code != 200:
        raise TwitchError(r.status_code, _msg(r))
    body = r.json()
    scope = body.get("scope") or []
    return TokenSet(body["access_token"], body.get("refresh_token"), body.get("expires_in"),
                    scope if isinstance(scope, list) else str(scope).split())


async def validate(access_token: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """Twitch requires apps to validate user tokens on start and hourly. None = token invalid."""
    async with _client(client) as c:
        r = await c.get(f"{ID}/validate", headers={"Authorization": f"OAuth {access_token}"})
    if r.status_code == 401:
        return None
    if r.status_code != 200:
        raise TwitchError(r.status_code, _msg(r))
    return dict(r.json())


async def revoke(token: str, client: httpx.AsyncClient | None = None) -> None:
    async with _client(client) as c:
        await c.post(f"{ID}/revoke", data={"client_id": get_settings().twitch_client_id, "token": token})


# --------------------------------------------------------------------------------------- Helix
class Helix:
    def __init__(self, access_token: str, client: httpx.AsyncClient | None = None) -> None:
        self.token = access_token
        self._client = client

    async def _req(self, method: str, path: str, *, params: Any = None, json: Any = None,
                   expected: tuple[int, ...] = (200,)) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token}", "Client-Id": get_settings().twitch_client_id}
        async with _client(self._client) as c:
            for attempt in range(4):
                r = await c.request(method, f"{HELIX}{path}", params=params, json=json, headers=headers)
                if r.status_code == 429 or r.status_code >= 500:
                    reset = r.headers.get("Ratelimit-Reset")
                    wait = 2**attempt
                    if r.status_code == 429 and reset and reset.isdigit():
                        import time

                        wait = max(1, int(reset) - int(time.time()))
                    log.warning("helix %s %s -> %s, retry in %ss", method, path, r.status_code, wait)
                    await asyncio.sleep(min(wait, 30))
                    continue
                break
        if r.status_code not in expected:
            raise TwitchError(r.status_code, _msg(r), _json(r))
        return r

    async def get_users(self, *, logins: list[str] | None = None, ids: list[str] | None = None
                        ) -> list[dict[str, Any]]:
        params = [("login", x) for x in logins or []] + [("id", x) for x in ids or []]
        return list((await self._req("GET", "/users", params=params)).json()["data"])

    async def get_stream(self, user_id: str) -> dict[str, Any] | None:
        data = (await self._req("GET", "/streams", params={"user_id": user_id, "type": "live"})).json()["data"]
        return data[0] if data else None

    async def get_current_vod(self, user_id: str, stream_id: str | None = None) -> dict[str, Any] | None:
        data = (await self._req("GET", "/videos", params={"user_id": user_id, "type": "archive", "first": 5})
                ).json()["data"]
        for v in data:
            if stream_id is None or v.get("stream_id") == stream_id:
                return dict(v)
        return None

    async def create_clip(self, broadcaster_id: str, *, title: str | None = None,
                          duration: float | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"broadcaster_id": broadcaster_id}
        if title:
            params["title"] = title[:100]
        if duration is not None:
            params["duration"] = _clip_duration(duration)
        try:
            r = await self._req("POST", "/clips", params=params, expected=(202,))
        except TwitchError as e:
            raise _classify_clip_error(e) from e
        return dict(r.json()["data"][0])

    async def create_clip_from_vod(self, *, editor_id: str, broadcaster_id: str, vod_id: str,
                                   vod_offset: int, duration: float, title: str) -> dict[str, Any]:
        duration = _clip_duration(duration)
        if vod_offset < duration:
            raise ValueError("vod_offset (clip end) must be >= duration")
        params = {"editor_id": editor_id, "broadcaster_id": broadcaster_id, "vod_id": vod_id,
                  "vod_offset": int(vod_offset), "duration": duration, "title": title[:100]}
        try:
            r = await self._req("POST", "/videos/clips", params=params, expected=(202,))
        except TwitchError as e:
            raise _classify_clip_error(e) from e
        return dict(r.json()["data"][0])

    async def get_clips(self, *, ids: list[str] | None = None, broadcaster_id: str | None = None,
                        started_at: str | None = None, ended_at: str | None = None, first: int = 20,
                        after: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        params: list[tuple[str, Any]] = [("id", i) for i in ids or []]
        if broadcaster_id:
            params.append(("broadcaster_id", broadcaster_id))
        if started_at:
            params.append(("started_at", started_at))
        if ended_at:
            params.append(("ended_at", ended_at))
        if not ids:
            params.append(("first", min(first, 100)))
        if after:
            params.append(("after", after))
        body = (await self._req("GET", "/clips", params=params)).json()
        return list(body["data"]), (body.get("pagination") or {}).get("cursor")

    async def get_clip_downloads(self, *, editor_id: str, broadcaster_id: str, clip_ids: list[str]
                                 ) -> list[dict[str, Any]]:
        if not 1 <= len(clip_ids) <= 10:
            raise ValueError("1-10 clip ids per request")
        params = [("editor_id", editor_id), ("broadcaster_id", broadcaster_id)] + [("clip_id", c) for c in clip_ids]
        return list((await self._req("GET", "/clips/downloads", params=params)).json()["data"])

    # EventSub management -------------------------------------------------------------------
    async def create_subscription(self, type_: str, version: str, condition: dict[str, str],
                                  transport: dict[str, str]) -> dict[str, Any]:
        body = {"type": type_, "version": version, "condition": condition, "transport": transport}
        r = await self._req("POST", "/eventsub/subscriptions", json=body, expected=(202, 409))
        return dict(r.json()) if r.status_code == 202 else {"conflict": True}

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        return list((await self._req("GET", "/eventsub/subscriptions")).json()["data"])

    async def delete_subscription(self, sub_id: str) -> None:
        await self._req("DELETE", "/eventsub/subscriptions", params={"id": sub_id}, expected=(204, 404))


def _clip_duration(d: float) -> float:
    return round(min(60.0, max(5.0, float(d))), 1)


def _classify_clip_error(e: TwitchError) -> TwitchError:
    if e.status == 403:
        return ClipRestricted(e.status, e.message, e.payload)
    if e.status == 404:
        return NotLive(e.status, e.message, e.payload)
    return e


def _msg(r: httpx.Response) -> str:
    j = _json(r)
    if isinstance(j, dict):
        return str(j.get("message") or j.get("error") or r.text[:300])
    return r.text[:300]


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return None


class _client:
    """Use the injected client (tests) or a short-lived one."""

    def __init__(self, client: httpx.AsyncClient | None) -> None:
        self.client = client
        self.owned: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self.client is not None:
            return self.client
        self.owned = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        return self.owned

    async def __aexit__(self, *exc: object) -> None:
        if self.owned is not None:
            await self.owned.aclose()
