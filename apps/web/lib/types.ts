/** Shapes returned by the Worker API (/api/v1). Timestamps are Unix seconds. */
export type Stream = {
  id: string; title: string | null; game: string | null; status: "live" | "ended"; started_at: number; ended_at: number | null;
  detected_via: string | null; clips_synced_at: number | null;
  stats: { viewer_clips?: number; selected?: number; duplicates?: number; below_threshold?: number };
};

export type Capabilities = {
  app_configured: boolean; accounts_connected: number; can_detect_live: boolean; can_list_clips: boolean;
  can_download_clips: boolean; media_account: string | null; notes: string[]; how: string;
};

export type ClipStatus = "discovered" | "duplicate" | "needs_media" | "media_ready" | "processing" | "rendered" | "failed" | "rejected";

export type Clip = {
  id: string; origin: "twitch_clip" | "viral_edit"; stream_id: string | null; title: string; clipper: string | null;
  view_count: number | null; score: number | null; selected: boolean; status: ClipStatus; status_detail: string | null;
  duration_s: number | null; twitch_url: string | null; category: string; caption: string | null; hashtags: string[];
  caption_source: string | null; created_at: number | null; updated_at: number | null; video_url: string | null;
  thumbnail_url: string | null; source_url: string | null; render_version: number; offset_s?: number;
};

export type Word = { w: string; start: number; end: number; emph?: boolean };
export type Cue = { start: number; end: number; zone: "lower" | "middle" | "upper"; words: Word[] };

export type ClipEdit = {
  layout?: "fit" | "crop"; crop_x?: number | null; zoom_intensity?: number; trim_start?: number; trim_end?: number | null;
  sub_size?: number; sub_color?: string; sub_highlight?: string; sub_uppercase?: boolean;
};

export type ClipDetail = Clip & {
  words: Word[]; cues: Cue[]; cues_edited: boolean; edit: ClipEdit; transcript: string | null; rights_basis: string | null;
  vod_offset: number | null;
  publishing: { id: string; status: string; mode: string; scheduled_for: number | null; fail_reason: string | null }[];
};

export type PublishingJob = {
  id: string; clip_id: string; clip_title: string; mode: "direct_post" | "inbox_draft"; status: string; title: string;
  privacy_level: string | null; scheduled_for: number | null; schedule_basis: string | null; publish_id: string | null;
  post_ids: string[]; fail_reason: string | null; confirmed_by: string | null; published_at: number | null;
  created_at: number; updated_at: number; thumbnail_url: string | null; video_url: string | null; clip_duration: number | null;
};

export type TikTokAccount = {
  open_id: string; display_name: string | null; username: string | null; avatar_url: string | null;
  creator_info: Record<string, unknown> | null; creator_info_at: number | null; scopes: string[]; revoked: boolean;
  connected_by: string | null;
};

export type PublishingSettings = {
  auto_publish: boolean; auto_mode: "inbox_draft" | "direct_post"; client_audited: boolean; max_posts_per_day: number;
  cooldown_minutes: number; timezone: string; automation_consent_by: string | null; automation_consent_at: number | null;
};

export type ProcessingSettings = {
  target_clips_max: number; min_views: number; min_clip_s: number; max_clip_s: number; default_layout: "fit" | "crop";
  zoom_intensity: number; auto_process: boolean;
};

export type Activity = { at: number; level: string; component: string; event: string; message: string };
