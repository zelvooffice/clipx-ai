"""Database schema (see docs/ARCHITECTURE.md §4.1)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _enum(e: type[enum.Enum]) -> Enum:
    return Enum(e, name=e.__name__.lower(), values_callable=lambda x: [m.value for m in x], native_enum=False,
                length=40)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- enums
class Provider(enum.StrEnum):
    twitch = "twitch"
    tiktok = "tiktok"


class TwitchRole(enum.StrEnum):
    operator = "operator"  # any account; can create native clips (clips:edit) and read chat
    editor = "editor"  # editor of the target channel; clip downloads + VOD clips
    broadcaster = "broadcaster"  # the target channel itself


class SessionStatus(enum.StrEnum):
    live = "live"
    finalizing = "finalizing"
    complete = "complete"


class SourceKind(enum.StrEnum):
    twitch_native = "twitch_native"
    live_ingest = "live_ingest"
    clip_media = "clip_media"
    vod_clip = "vod_clip"


class SourceStatus(enum.StrEnum):
    available = "available"
    active = "active"
    unavailable = "unavailable"
    degraded = "degraded"


class CandidateStatus(enum.StrEnum):
    provisional = "provisional"  # single-family signal (e.g. chat-only): needs media verification
    accepted = "accepted"
    rejected = "rejected"
    duplicate = "duplicate"
    clipped = "clipped"


class ClipOrigin(enum.StrEnum):
    stream_clip = "stream_clip"
    viral_edit = "viral_edit"


class TwitchClipStatus(enum.StrEnum):
    not_requested = "not_requested"
    pending = "pending"
    created = "created"
    failed = "failed"


class EditStatus(enum.StrEnum):
    needs_media = "needs_media"
    queued = "queued"
    transcribing = "transcribing"
    rendering = "rendering"
    rendered = "rendered"
    failed = "failed"
    rejected = "rejected"


class MediaSource(enum.StrEnum):
    none = "none"
    live_buffer = "live_buffer"
    twitch_download = "twitch_download"
    upload = "upload"
    composed = "composed"


class Verification(enum.StrEnum):
    verified = "verified"  # passed multi-signal analysis on actual media
    unverified = "unverified"  # chat-signal only, media not analysed


class PublishMode(enum.StrEnum):
    direct_post = "direct_post"
    inbox_draft = "inbox_draft"


class PublishStatus(enum.StrEnum):
    awaiting_confirmation = "awaiting_confirmation"
    scheduled = "scheduled"
    uploading = "uploading"
    processing = "processing"
    sent_to_inbox = "sent_to_inbox"
    published = "published"
    failed = "failed"
    cancelled = "cancelled"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"  # will retry
    dead = "dead"  # exhausted attempts


class RightsStatus(enum.StrEnum):
    unknown = "unknown"
    authorized = "authorized"  # official download permitted for our editor/broadcaster token
    metadata_only = "metadata_only"


# --------------------------------------------------------------------------- identity & auth
class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthorizedEmail(TimestampMixin, Base):
    __tablename__ = "authorized_emails"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_by: Mapped[str | None] = mapped_column(String(320))


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[Provider] = mapped_column(_enum(Provider), nullable=False)
    requested_role: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OAuthToken(TimestampMixin, Base):
    __tablename__ = "oauth_tokens"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    provider: Mapped[Provider] = mapped_column(_enum(Provider), nullable=False)
    subject: Mapped[str] = mapped_column(String(120), nullable=False)  # twitch user id / tiktok open_id
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_token_provider_subject"),)


class TwitchAccount(TimestampMixin, Base):
    __tablename__ = "twitch_accounts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    twitch_user_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    login: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[TwitchRole] = mapped_column(_enum(TwitchRole), nullable=False)
    is_editor_verified: Mapped[bool | None] = mapped_column(Boolean)  # None = not yet checked
    token_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("oauth_tokens.id", ondelete="SET NULL"))
    connected_by: Mapped[str | None] = mapped_column(String(320))
    token: Mapped[OAuthToken | None] = relationship(lazy="joined")


class TikTokAccount(TimestampMixin, Base):
    __tablename__ = "tiktok_accounts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    open_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    username: Mapped[str | None] = mapped_column(String(120))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    creator_info: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    creator_info_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("oauth_tokens.id", ondelete="SET NULL"))
    connected_by: Mapped[str | None] = mapped_column(String(320))
    token: Mapped[OAuthToken | None] = relationship(lazy="joined")


# --------------------------------------------------------------------------- streams & sources
class StreamSession(TimestampMixin, Base):
    __tablename__ = "stream_sessions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    broadcaster_id: Mapped[str] = mapped_column(String(40), nullable=False)
    twitch_stream_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    game_name: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SessionStatus] = mapped_column(_enum(SessionStatus), default=SessionStatus.live)
    vod_id: Mapped[str | None] = mapped_column(String(64))
    detected_via: Mapped[str] = mapped_column(String(40), default="eventsub")
    analysis_mode: Mapped[str] = mapped_column(String(40), default="none")  # full_av | chat_only | none
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    ingest_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_stream_sessions_status_started", "status", "started_at"),)


class VideoSource(TimestampMixin, Base):
    __tablename__ = "video_sources"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    kind: Mapped[SourceKind] = mapped_column(_enum(SourceKind), unique=True, nullable=False)
    status: Mapped[SourceStatus] = mapped_column(_enum(SourceStatus), default=SourceStatus.unavailable)
    detail: Mapped[str | None] = mapped_column(Text)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HighlightCandidate(TimestampMixin, Base):
    __tablename__ = "highlight_candidates"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stream_sessions.id", ondelete="CASCADE"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    peak_offset_s: Mapped[float] = mapped_column(Float, nullable=False)  # seconds since stream start
    start_offset_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_offset_s: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    context_summary: Mapped[str | None] = mapped_column(Text)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    component_scores: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    signals_used: Mapped[list[str]] = mapped_column(JSONType, default=list)
    transcript_excerpt: Mapped[str | None] = mapped_column(Text)
    analyzer: Mapped[str] = mapped_column(String(40), default="heuristic")  # llm | heuristic
    status: Mapped[CandidateStatus] = mapped_column(_enum(CandidateStatus), nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(80), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    session: Mapped[StreamSession] = relationship()
    __table_args__ = (
        UniqueConstraint("session_id", "dedupe_key", name="uq_candidate_session_dedupe"),
        Index("ix_candidates_session_status", "session_id", "status"),
    )


# --------------------------------------------------------------------------- clips & editing
class Clip(TimestampMixin, Base):
    __tablename__ = "clips"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    origin: Mapped[ClipOrigin] = mapped_column(_enum(ClipOrigin), default=ClipOrigin.stream_clip)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stream_sessions.id", ondelete="SET NULL"))
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("highlight_candidates.id", ondelete="SET NULL"), unique=True
    )
    twitch_clip_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    twitch_edit_url: Mapped[str | None] = mapped_column(Text)
    twitch_clip_url: Mapped[str | None] = mapped_column(Text)
    twitch_status: Mapped[TwitchClipStatus] = mapped_column(
        _enum(TwitchClipStatus), default=TwitchClipStatus.not_requested
    )
    twitch_error: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="moment")
    quality_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    verification: Mapped[Verification] = mapped_column(_enum(Verification), default=Verification.unverified)
    start_offset_s: Mapped[float | None] = mapped_column(Float)
    duration_s: Mapped[float | None] = mapped_column(Float)
    media_source: Mapped[MediaSource] = mapped_column(_enum(MediaSource), default=MediaSource.none)
    rights_basis: Mapped[str | None] = mapped_column(Text)
    source_media_key: Mapped[str | None] = mapped_column(Text)
    rendered_key: Mapped[str | None] = mapped_column(Text)
    thumbnail_key: Mapped[str | None] = mapped_column(Text)
    render_meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    edit_status: Mapped[EditStatus] = mapped_column(_enum(EditStatus), default=EditStatus.needs_media)
    edit_error: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    caption_source: Mapped[str | None] = mapped_column(String(40))
    transcript_text: Mapped[str | None] = mapped_column(Text)
    session: Mapped[StreamSession | None] = relationship()
    candidate: Mapped[HighlightCandidate | None] = relationship()
    __table_args__ = (Index("ix_clips_origin_created", "origin", "created_at"),)


class SubtitleTrack(TimestampMixin, Base):
    __tablename__ = "subtitle_tracks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), nullable=False)
    language: Mapped[str | None] = mapped_column(String(12))
    engine: Mapped[str] = mapped_column(String(60), nullable=False)
    words: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)  # [{w,start,end,p}]
    cues: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)  # grouped, editable
    version: Mapped[int] = mapped_column(Integer, default=1)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (Index("ix_subtitles_clip_active", "clip_id", "is_active"),)


class EditConfig(TimestampMixin, Base):
    __tablename__ = "edit_configs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), unique=True)
    layout: Mapped[str] = mapped_column(String(20), default="auto")  # auto|track|stacked|fit
    zoom_intensity: Mapped[float] = mapped_column(Float, default=1.0)  # 0 disables punch-ins
    sfx_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    subtitle_style: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    crop_override: Mapped[dict[str, Any] | None] = mapped_column(JSONType)  # {x_center: 0..1}
    trim_start_s: Mapped[float] = mapped_column(Float, default=0.0)
    trim_end_s: Mapped[float | None] = mapped_column(Float)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONType)  # last generated edit plan
    version: Mapped[int] = mapped_column(Integer, default=1)


# --------------------------------------------------------------------------- viral edits
class ViralSourceVideo(TimestampMixin, Base):
    __tablename__ = "viral_source_videos"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    creator: Mapped[str | None] = mapped_column(String(160))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    view_count: Mapped[int | None] = mapped_column(Integer)  # None = not provided by the source
    engagement: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    duration_s: Mapped[float | None] = mapped_column(Float)
    popularity_score: Mapped[float] = mapped_column(Float, default=0.0)
    rights_status: Mapped[RightsStatus] = mapped_column(_enum(RightsStatus), default=RightsStatus.unknown)
    discovered_via: Mapped[str] = mapped_column(String(80), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    linked_clip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"))
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_viral_platform_external"),)


class ViralEdit(TimestampMixin, Base):
    __tablename__ = "viral_edits"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    template_key: Mapped[str] = mapped_column(String(60), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    input_clip_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    viral_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("viral_source_videos.id", ondelete="SET NULL")
    )
    output_clip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    suggested_by: Mapped[str] = mapped_column(String(40), default="user")


# --------------------------------------------------------------------------- publishing
class PublishingJob(TimestampMixin, Base):
    __tablename__ = "publishing_jobs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), nullable=False)
    tiktok_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tiktok_accounts.id", ondelete="CASCADE"))
    mode: Mapped[PublishMode] = mapped_column(_enum(PublishMode), nullable=False)
    status: Mapped[PublishStatus] = mapped_column(_enum(PublishStatus), nullable=False)
    title: Mapped[str] = mapped_column(Text, default="")
    privacy_level: Mapped[str | None] = mapped_column(String(40))
    allow_comment: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_duet: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_stitch: Mapped[bool] = mapped_column(Boolean, default=False)
    brand_content_toggle: Mapped[bool] = mapped_column(Boolean, default=False)
    brand_organic_toggle: Mapped[bool] = mapped_column(Boolean, default=False)
    is_aigc: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String(320))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schedule_basis: Mapped[str | None] = mapped_column(Text)
    transfer_method: Mapped[str | None] = mapped_column(String(20))
    publish_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    post_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    fail_reason: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clip: Mapped[Clip] = relationship()
    __table_args__ = (
        Index(
            "uq_publish_active_clip_account",
            "clip_id",
            "tiktok_account_id",
            unique=True,
            postgresql_where=text("status NOT IN ('failed','cancelled')"),
            sqlite_where=text("status NOT IN ('failed','cancelled')"),
        ),
        Index("ix_publish_status_scheduled", "status", "scheduled_for"),
    )


class PublishingHistory(Base):
    __tablename__ = "publishing_history"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("publishing_jobs.id", ondelete="CASCADE"))
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_pubhist_job_at", "job_id", "at"),)


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    tiktok_video_id: Mapped[str] = mapped_column(String(80), nullable=False)
    clip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"))
    publishing_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("publishing_jobs.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float | None] = mapped_column(Float)
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    share_url: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_analytics_video_captured", "tiktok_video_id", "captured_at"),)


# --------------------------------------------------------------------------- operations
class ProcessingJob(TimestampMixin, Base):
    __tablename__ = "processing_jobs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    ref_id: Mapped[str | None] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.queued)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(120))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_kind_ref", "kind", "ref_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_audit_at", "at"),)


class SystemLog(Base):
    __tablename__ = "system_logs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    component: Mapped[str] = mapped_column(String(60), nullable=False)
    event: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stream_sessions.id", ondelete="SET NULL"))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_syslog_at", "at"), Index("ix_syslog_session_at", "session_id", "at"))


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONType, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(320))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
