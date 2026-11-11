"""FIXTURE — NOT REAL DATA. Response shapes copied from the examples in TikTok's official Content Posting /
Login Kit / Display API documentation (developers.tiktok.com, retrieved 2026-11-11). Contract tests only."""

OK = {"code": "ok", "message": "", "log_id": "202210112248442CB9319E1FB30C1073F3"}
TOKEN = {"access_token": "act.fixture1", "expires_in": 7200, "open_id": "fixture-open-id",
         "refresh_expires_in": 15552000, "refresh_token": "rft.fixture1",
         "scope": "user.info.basic,video.publish,video.upload,video.list", "token_type": "Bearer"}
TOKEN_REFRESHED = {**TOKEN, "access_token": "act.fixture2", "refresh_token": "rft.fixture2"}
USER_INFO = {"data": {"user": {"open_id": "fixture-open-id", "display_name": "FIXTURE Clips",
                               "avatar_url": "https://example.invalid/a.png"}}, "error": OK}
CREATOR_INFO = {"data": {"creator_avatar_url": "https://example.invalid/a.png", "creator_username": "fixtureclips",
                         "creator_nickname": "FIXTURE Clips",
                         "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                         "comment_disabled": False, "duet_disabled": False, "stitch_disabled": True,
                         "max_video_post_duration_sec": 300}, "error": OK}
INIT = {"data": {"publish_id": "v_pub_file~v2-1.123456789",
                 "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=67890&upload_token=Xza123"},
        "error": OK}
STATUS_PROCESSING = {"data": {"status": "PROCESSING_UPLOAD", "uploaded_bytes": 10000}, "error": OK}
STATUS_COMPLETE = {"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7300000000000000001]},
                   "error": OK}
STATUS_INBOX = {"data": {"status": "SEND_TO_USER_INBOX"}, "error": OK}
UNAUDITED = {"data": {}, "error": {"code": "unaudited_client_can_only_post_to_private_accounts",
                                   "message": "FIXTURE", "log_id": "x"}}
VIDEO_LIST = {"data": {"videos": [
    {"id": "7300000000000000001", "create_time": 1762000000, "title": "FIXTURE", "duration": 20,
     "view_count": 1200, "like_count": 100, "comment_count": 10, "share_count": 5, "share_url": "https://example.invalid/v"}],
    "cursor": 0, "has_more": False}, "error": OK}
