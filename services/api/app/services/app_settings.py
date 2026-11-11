"""Runtime settings editable from the dashboard (validated, audited, with safe defaults)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.models import AppSetting


class ScoringWeights(BaseModel):
    storytelling_context: float = Field(0.30, ge=0, le=1)
    visual_interest: float = Field(0.25, ge=0, le=1)
    audio_speech: float = Field(0.15, ge=0, le=1)
    audience_reaction: float = Field(0.15, ge=0, le=1)
    engagement_potential: float = Field(0.15, ge=0, le=1)

    @model_validator(mode="after")
    def _sum(self) -> ScoringWeights:
        total = sum(self.model_dump().values())
        if not 0.99 <= total <= 1.01:
            raise ValueError(f"weights must sum to 1.0 (got {total:.2f})")
        return self


class ProcessingSettings(BaseModel):
    weights: ScoringWeights = ScoringWeights()
    min_quality: float = Field(0.62, ge=0, le=1)
    min_confidence: float = Field(0.35, ge=0, le=1)
    target_clips_min: int = Field(15, ge=0, le=100)
    target_clips_max: int = Field(25, ge=1, le=100)
    min_clip_s: float = Field(8.0, ge=5, le=60)
    max_clip_s: float = Field(60.0, ge=5, le=60)
    mirror_as_twitch_clips: bool = True
    use_llm: bool = True

    @model_validator(mode="after")
    def _ranges(self) -> ProcessingSettings:
        if self.target_clips_min > self.target_clips_max:
            raise ValueError("target_clips_min must be <= target_clips_max")
        if self.min_clip_s >= self.max_clip_s:
            raise ValueError("min_clip_s must be < max_clip_s")
        return self


class PublishingSettings(BaseModel):
    auto_publish: bool = False
    # inbox_draft: fully automatic draft delivery (creator taps post in TikTok).
    # direct_post: prepared automatically, then a TikTok-compliant one-click confirmation per post.
    auto_mode: Literal["inbox_draft", "direct_post"] = "inbox_draft"
    client_audited: bool = False  # set true only once TikTok has approved the app audit
    max_posts_per_day: int = Field(6, ge=1, le=15)
    cooldown_minutes: int = Field(90, ge=10, le=24 * 60)
    min_quality_to_publish: float = Field(0.7, ge=0, le=1)
    timezone: str = "UTC"
    require_verified_clips: bool = True
    # Express, recorded consent for automatic inbox-draft delivery (TikTok guidelines §5c).
    automation_consent_by: str | None = None
    automation_consent_at: str | None = None


class NotificationSettings(BaseModel):
    email_on_failure: bool = False
    email_on_stream_summary: bool = False


DEFAULTS: dict[str, type[BaseModel]] = {
    "processing": ProcessingSettings,
    "publishing": PublishingSettings,
    "notifications": NotificationSettings,
}


def get_section(db: Session, key: str) -> Any:
    model = DEFAULTS[key]
    row = db.get(AppSetting, key)
    return model.model_validate(row.value) if row else model()


def update_section(db: Session, key: str, patch: dict[str, Any], actor: str) -> BaseModel:
    model = DEFAULTS[key]
    current = get_section(db, key).model_dump()
    merged = _deep_merge(current, patch)
    validated = model.model_validate(merged)
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=validated.model_dump(), updated_by=actor))
    else:
        row.value = validated.model_dump()
        row.updated_by = actor
    return validated


def _deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
