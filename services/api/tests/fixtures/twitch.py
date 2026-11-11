"""FIXTURE — NOT REAL DATA. Response shapes copied from the examples in the official Twitch API reference
(https://dev.twitch.tv/docs/api/reference, retrieved 2026-11-11). Used only by contract tests."""

USER_LACY = {"id": "141981764", "login": "lacy", "display_name": "Lacy", "type": "", "broadcaster_type": "partner",
             "description": "FIXTURE", "profile_image_url": "https://example.invalid/p.png",
             "offline_image_url": "", "created_at": "2016-12-14T20:32:28Z"}
STREAM_LIVE = {"id": "40952121085", "user_id": "141981764", "user_login": "lacy", "user_name": "Lacy",
               "game_id": "509658", "game_name": "Just Chatting", "type": "live", "title": "FIXTURE stream title",
               "tags": [], "viewer_count": 1, "started_at": "2026-11-11T12:00:00Z", "language": "en",
               "thumbnail_url": "", "tag_ids": [], "is_mature": False}
CREATE_CLIP = {"data": [{"id": "FiveWordsForClipSlug", "edit_url": "https://www.twitch.tv/twitchdev/clip/FiveWordsForClipSlug"}]}
GET_CLIP = {"id": "FiveWordsForClipSlug", "url": "https://clips.twitch.tv/FiveWordsForClipSlug",
            "embed_url": "", "broadcaster_id": "141981764", "broadcaster_name": "Lacy", "creator_id": "1",
            "creator_name": "fixture_user", "video_id": "", "game_id": "509658", "language": "en",
            "title": "FIXTURE clip", "view_count": 10, "created_at": "2026-11-11T12:30:00Z", "thumbnail_url": "",
            "duration": 30.0, "vod_offset": 1770, "is_featured": False}
CLIP_DOWNLOAD = {"data": [{"clip_id": "FiveWordsForClipSlug",
                           "landscape_download_url": "https://production.assets.clips.twitchcdn.net/fixture.mp4",
                           "portrait_download_url": None}]}
VALIDATE = {"client_id": "test-client-id", "login": "operator1", "scopes": ["clips:edit", "user:read:chat"],
            "user_id": "999", "expires_in": 14000}
TOKEN = {"access_token": "fixture-access", "refresh_token": "fixture-refresh", "expires_in": 14000,
         "scope": ["clips:edit", "user:read:chat"], "token_type": "bearer"}
APP_TOKEN = {"access_token": "fixture-app-token", "expires_in": 5000000, "token_type": "bearer"}
