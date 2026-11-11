"""TikTok Login Kit + Content Posting API + Display API client.

Verified against developers.tiktok.com (2026-11-11):
* Authorize: https://www.tiktok.com/v2/auth/authorize/ (client_key, scope comma-separated, redirect_uri,
  state, response_type=code). Token: POST /v2/oauth/token/. Access token 2 h, refresh token 180 days and
  ROTATES on every refresh (always store the returned refresh token).
* POST /v2/post/publish/creator_info/query/ -> creator_nickname, privacy_level_options, *_disabled,
  max_video_post_duration_sec.
* POST /v2/post/publish/video/init/ (video.publish, Direct Post; 6 req/min per token) with post_info +
  source_info (FILE_UPLOAD | PULL_FROM_URL). Unaudited clients: SELF_ONLY only.
* POST /v2/post/publish/inbox/video/init/ (video.upload): draft to the creator's inbox.
* PUT upload_url with Content-Range; chunks 5-64 MB (last up to 128 MB), < 5 MB whole, sequential;
  total_chunk_count = floor(video_size / chunk_size). 206 per chunk, 201 when complete.
* POST /v2/post/publish/status/fetch/ (30 req/min): PROCESSING_UPLOAD | PROCESSING_DOWNLOAD |
  SEND_TO_USER_INBOX | PUBLISH_COMPLETE | FAILED (+ fail_reason, publicaly_available_post_id).
* POST /v2/video/list/ and /v2/video/query/ (video.list): view_count, like_count, comment_count, share_count.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings

log = logging.getLogger("clipx.tiktok")

API = "https://open.tiktokapis.com"
AUTHORIZE = "https://www.tiktok.com/v2/auth/authorize/"
MB = 1024 * 1024
MIN_CHUNK, MAX_CHUNK = 5 * MB, 64 * MB
JSON_CT = "application/json; charset=UTF-8"


class TikTokError(Exception):
    def __init__(self, http_status: int, code: str, message: str, log_id: str | None = None) -> None:
        super().__init__(f"TikTok {http_status} {code}: {message}")
        self.http_status = http_status
        self.code = code
        self.message = message
        self.log_id = log_id

    @property
    def retryable(self) -> bool:
        return self.http_status == 429 or self.http_status >= 500 or self.code in ("rate_limit_exceeded",
                                                                                    "internal_error")


@dataclass
class TikTokTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    refresh_expires_in: int | None
    open_id: str
    scopes: list[str]


def authorize_url(state: str) -> str:
    s = get_settings()
    q = {"client_key": s.tiktok_client_key, "scope": s.tiktok_scopes, "response_type": "code",
         "redirect_uri": s.tiktok_redirect_uri, "state": state}
    return f"{AUTHORIZE}?{urlencode(q)}"


def chunk_plan(video_size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) following the media transfer rules."""
    if video_size <= 0:
        raise ValueError("empty video")
    if video_size <= MAX_CHUNK:  # < 5 MB must be whole; up to 64 MB may be one chunk
        return video_size, 1
    chunk = 10 * MB
    count = video_size // chunk  # remainder (< 10 MB) is merged into the final chunk (<= 20 MB < 128 MB)
    if count > 1000:
        chunk = MAX_CHUNK
        count = video_size // chunk
    return chunk, int(count)


def chunk_ranges(video_size: int, chunk: int, count: int) -> list[tuple[int, int]]:
    ranges = []
    for i in range(count):
        first = i * chunk
        last = video_size - 1 if i == count - 1 else first + chunk - 1
        ranges.append((first, last))
    return ranges


class TikTokClient:
    def __init__(self, access_token: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.token = access_token
        self._client = client

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _call(self, method: str, path: str, *, json: Any = None, params: Any = None) -> dict[str, Any]:
        c = await self._http()
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": JSON_CT}
        for attempt in range(3):
            r = await c.request(method, f"{API}{path}", json=json, params=params, headers=headers)
            body = _json(r)
            err = (body or {}).get("error") or {}
            code = err.get("code", "ok" if r.status_code == 200 else "http_error")
            if r.status_code == 200 and code == "ok":
                return dict(body.get("data") or {})
            exc = TikTokError(r.status_code, code, err.get("message") or r.text[:300], err.get("log_id"))
            if exc.retryable and attempt < 2:
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            raise exc
        raise AssertionError("unreachable")

    # -- OAuth --------------------------------------------------------------------------------
    async def _token(self, data: dict[str, str]) -> TikTokTokens:
        s = get_settings()
        c = await self._http()
        r = await c.post(f"{API}/v2/oauth/token/", data={"client_key": s.tiktok_client_key,
                                                          "client_secret": s.tiktok_client_secret, **data},
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        body = _json(r) or {}
        if r.status_code != 200 or "access_token" not in body:
            raise TikTokError(r.status_code, str(body.get("error", "token_error")),
                              str(body.get("error_description", r.text[:300])), body.get("log_id"))
        return TikTokTokens(body["access_token"], body.get("refresh_token"), body.get("expires_in"),
                            body.get("refresh_expires_in"), body["open_id"],
                            [x for x in str(body.get("scope", "")).split(",") if x])

    async def exchange_code(self, code: str) -> TikTokTokens:
        return await self._token({"code": code, "grant_type": "authorization_code",
                                  "redirect_uri": get_settings().tiktok_redirect_uri})

    async def refresh(self, refresh_token: str) -> TikTokTokens:
        return await self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})

    async def revoke(self) -> None:
        s = get_settings()
        c = await self._http()
        await c.post(f"{API}/v2/oauth/revoke/", data={"client_key": s.tiktok_client_key,
                                                       "client_secret": s.tiktok_client_secret, "token": self.token},
                     headers={"Content-Type": "application/x-www-form-urlencoded"})

    # -- user / creator -------------------------------------------------------------------------
    async def user_info(self) -> dict[str, Any]:
        data = await self._call("GET", "/v2/user/info/", params={"fields": "open_id,avatar_url,display_name"})
        return dict(data.get("user") or {})

    async def creator_info(self) -> dict[str, Any]:
        return await self._call("POST", "/v2/post/publish/creator_info/query/", json={})

    # -- posting ---------------------------------------------------------------------------------
    async def init_direct_post(self, post_info: dict[str, Any], source_info: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v2/post/publish/video/init/",
                                json={"post_info": post_info, "source_info": source_info})

    async def init_inbox_upload(self, source_info: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/v2/post/publish/inbox/video/init/", json={"source_info": source_info})

    async def upload_file(self, upload_url: str, path: Path, chunk: int, count: int) -> None:
        size = await asyncio.to_thread(lambda: path.stat().st_size)
        c = await self._http()
        with path.open("rb") as f:
            for i, (first, last) in enumerate(chunk_ranges(size, chunk, count)):
                f.seek(first)
                data = f.read(last - first + 1)
                headers = {"Content-Type": "video/mp4", "Content-Length": str(len(data)),
                           "Content-Range": f"bytes {first}-{last}/{size}"}
                for attempt in range(3):
                    r = await c.put(upload_url, content=data, headers=headers, timeout=300)
                    if r.status_code in (201, 206):
                        break
                    if r.status_code >= 500 and attempt < 2:
                        await asyncio.sleep(2 ** (attempt + 1))
                        continue
                    raise TikTokError(r.status_code, "upload_failed", f"chunk {i + 1}/{count}: {r.text[:200]}")

    async def fetch_status(self, publish_id: str) -> dict[str, Any]:
        return await self._call("POST", "/v2/post/publish/status/fetch/", json={"publish_id": publish_id})

    # -- analytics -------------------------------------------------------------------------------
    VIDEO_FIELDS = "id,create_time,title,video_description,duration,share_url,view_count,like_count," \
                   "comment_count,share_count"

    async def list_videos(self, cursor: int | None = None, max_count: int = 20) -> dict[str, Any]:
        body: dict[str, Any] = {"max_count": min(20, max_count)}
        if cursor:
            body["cursor"] = cursor
        return await self._call("POST", "/v2/video/list/", params={"fields": self.VIDEO_FIELDS}, json=body)

    async def query_videos(self, ids: list[str]) -> list[dict[str, Any]]:
        data = await self._call("POST", "/v2/video/query/", params={"fields": self.VIDEO_FIELDS},
                                json={"filters": {"video_ids": ids[:20]}})
        return list(data.get("videos") or [])


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return None
