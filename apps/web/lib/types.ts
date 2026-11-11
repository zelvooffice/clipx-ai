export type Session = {
  id: string; twitch_stream_id: string; title: string | null; game: string | null; status: string;
  started_at: string | null; ended_at: string | null; analysis_mode: string; detected_via: string;
  stats: Record<string, number | null>; signals: string[];
};

export type Capabilities = {
  app_configured: boolean; accounts_connected: number; can_detect_live: boolean; can_create_clips: boolean;
  can_read_chat: boolean; can_download_clips: boolean; can_vod_clip: boolean; live_ingest_configured: boolean;
  notes: string[];
};

export type Candidate = {
  id: string; peak_offset_s: number; start_offset_s: number; end_offset_s: number; category: string;
  title: string | null; explanation: string; context_summary: string | null; quality_score: number; confidence: number;
  components: Record<string, number | null>; signals: Record<string, number | boolean>; supporting_families: string[];
  signals_used: string[]; analyzer: string; status: string; reject_reason: string | null; latency_ms: number | null;
  transcript_excerpt: string | null; detected_at: string | null;
};

export type Clip = {
  id: string; origin: "stream_clip" | "viral_edit"; session_id: string | null; title: string; category: string;
  quality_score: number | null; confidence: number | null; verification: "verified" | "unverified";
  duration_s: number | null; edit_status: string; edit_error: string | null; twitch_status: string;
  twitch_clip_url: string | null; twitch_error: string | null; media_source: string; caption: string | null;
  hashtags: string[]; caption_source: string | null; created_at: string | null; thumbnail_url: string | null;
  video_url: string | null; layout: string | null;
};

export type Cue = { start: number; end: number; zone: string; words: { w: string; start: number; end: number; emph: boolean }[] };

export type ClipDetail = Clip & {
  rights_basis: string | null; source_url: string | null; transcript: string | null; twitch_edit_url: string | null; start_offset_s: number | null;
  candidate: Candidate | null;
  edit_config: null | {
    layout: string; zoom_intensity: number; sfx_enabled: boolean; crop_override: { x_center: number } | null;
    trim_start_s: number; trim_end_s: number | null; version: number;
    subtitle_style: { size: number; primary: string; highlight: string; outline: number; uppercase: boolean; emphasis: boolean };
    plan: null | { style: string; intensity: number; zooms: unknown[]; sfx: unknown[]; notes: string[] };
  };
  subtitles: null | { id: string; engine: string; language: string | null; edited: boolean; version: number; cues: Cue[]; word_count: number };
  publishing: { id: string; status: string; mode: string; scheduled_for: string | null; fail_reason: string | null }[];
  processing: { kind: string; status: string; attempts: number; error: string | null; updated_at: string | null }[];
};

export type PublishingJob = {
  id: string; clip: Clip; mode: string; status: string; title: string; privacy_level: string | null;
  scheduled_for: string | null; schedule_basis: string | null; transfer_method: string | null; publish_id: string | null;
  post_ids: string[]; fail_reason: string | null; confirmed_by: string | null; published_at: string | null; created_at: string | null;
};

export type TikTokAccount = {
  id: string; display_name: string | null; username: string | null; avatar_url: string | null;
  creator_info: Record<string, unknown> | null; creator_info_at: string | null; scopes: string[]; revoked: boolean;
  token_expires_at: string | null; connected_by: string | null;
};

export type PublishingSettings = {
  auto_publish: boolean; auto_mode: "inbox_draft" | "direct_post"; client_audited: boolean; max_posts_per_day: number;
  cooldown_minutes: number; min_quality_to_publish: number; timezone: string; require_verified_clips: boolean;
  automation_consent_by: string | null; automation_consent_at: string | null;
};
