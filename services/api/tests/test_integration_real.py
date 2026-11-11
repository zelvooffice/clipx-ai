"""Real-credential integration tests. Skipped (never faked) unless credentials are provided:
    REAL_TWITCH_CLIENT_ID / REAL_TWITCH_CLIENT_SECRET, REAL_TIKTOK_ACCESS_TOKEN
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

TW_ID, TW_SECRET = os.environ.get("REAL_TWITCH_CLIENT_ID"), os.environ.get("REAL_TWITCH_CLIENT_SECRET")


@pytest.mark.skipif(not (TW_ID and TW_SECRET), reason="REAL_TWITCH_CLIENT_ID/SECRET not set")
async def test_real_twitch_app_token_and_target_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.integrations.twitch import helix

    monkeypatch.setenv("TWITCH_CLIENT_ID", TW_ID or "")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", TW_SECRET or "")
    get_settings.cache_clear()
    tok = await helix.app_token()
    api = helix.Helix(tok.access_token)
    users = await api.get_users(logins=[get_settings().twitch_target_login])
    assert users and users[0]["login"].lower() == get_settings().twitch_target_login.lower()
    await api.get_stream(users[0]["id"])  # live or not, the call must succeed
    clips, _ = await api.get_clips(broadcaster_id=users[0]["id"], first=5)
    assert isinstance(clips, list)


@pytest.mark.skipif(not os.environ.get("REAL_TIKTOK_ACCESS_TOKEN"), reason="REAL_TIKTOK_ACCESS_TOKEN not set")
async def test_real_tiktok_creator_info() -> None:
    from app.integrations.tiktok.client import TikTokClient

    c = TikTokClient(os.environ["REAL_TIKTOK_ACCESS_TOKEN"])
    try:
        info = await c.creator_info()
    finally:
        await c.aclose()
    assert "privacy_level_options" in info
