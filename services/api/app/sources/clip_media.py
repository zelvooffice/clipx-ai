"""Authorized clip media retrieval via Twitch Get Clips Download (never scraping)."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.integrations.twitch import helix
from app.models import TwitchAccount
from app.services import twitch_service

log = logging.getLogger("clipx.clipmedia")


class MediaUnavailable(Exception):
    """No authorized way to obtain this clip's media."""


async def download_clip(db: Session, broadcaster_id: str, clip_id: str, dest: Path) -> tuple[Path, str]:
    account = twitch_service.best_account(db, needs="download")
    if account is None:
        raise MediaUnavailable(
            "Twitch clip media requires broadcaster or editor authorization (Get Clips Download)."
        )
    token = await twitch_service.get_user_access_token(db, account)
    api = helix.Helix(token)
    try:
        items = await api.get_clip_downloads(editor_id=account.twitch_user_id, broadcaster_id=broadcaster_id,
                                             clip_ids=[clip_id])
    except helix.TwitchError as exc:
        if exc.status == 403:
            _mark_not_editor(account)
            raise MediaUnavailable(f"{account.login} is not an editor of the channel") from exc
        raise
    if account.is_editor_verified is None:
        account.is_editor_verified = True
    if not items:
        raise MediaUnavailable("clip not found or not accessible to this editor")
    url = items[0].get("landscape_download_url") or items[0].get("portrait_download_url")
    if not url:
        raise MediaUnavailable("Twitch returned no download URL for this clip (yet)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True) as c:
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    f.write(chunk)
    basis = f"Twitch Get Clips Download as {account.role.value} '{account.login}'"
    return dest, basis


def _mark_not_editor(account: TwitchAccount) -> None:
    account.is_editor_verified = False
