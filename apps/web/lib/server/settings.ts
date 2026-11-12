/// <reference types="@cloudflare/workers-types" />
/** Runtime settings stored in D1, validated on write. */
import { j, now, one, run, s } from "./db";

export interface ProcessingSettings {
  target_clips_max: number;   // never padded: fewer good moments -> fewer clips
  min_views: number;          // a viewer clip needs at least this many views to be considered
  min_clip_s: number;
  max_clip_s: number;
  default_layout: "fit" | "crop";
  zoom_intensity: number;
  auto_process: boolean;      // process queued clips automatically while a dashboard tab is open
}

export interface PublishingSettings {
  auto_publish: boolean;
  auto_mode: "inbox_draft" | "direct_post";
  client_audited: boolean;
  max_posts_per_day: number;
  cooldown_minutes: number;
  timezone: string;
  automation_consent_by: string | null;
  automation_consent_at: number | null;
}

export const DEFAULTS = {
  processing: { target_clips_max: 25, min_views: 2, min_clip_s: 8, max_clip_s: 60, default_layout: "fit",
                zoom_intensity: 1, auto_process: true } as ProcessingSettings,
  publishing: { auto_publish: false, auto_mode: "inbox_draft", client_audited: false, max_posts_per_day: 6,
                cooldown_minutes: 90, timezone: "UTC", automation_consent_by: null, automation_consent_at: null } as PublishingSettings,
};
export type Section = keyof typeof DEFAULTS;

export async function getSection<K extends Section>(db: D1Database, key: K): Promise<(typeof DEFAULTS)[K]> {
  const row = await one<{ value: string }>(db, "SELECT value FROM settings WHERE key = ?", key);
  return { ...DEFAULTS[key], ...j<Partial<(typeof DEFAULTS)[K]>>(row?.value, {}) };
}

const num = (v: unknown, lo: number, hi: number, name: string) => {
  if (typeof v !== "number" || !Number.isFinite(v) || v < lo || v > hi) throw new Error(`${name} must be between ${lo} and ${hi}`);
  return v;
};

export function validate<K extends Section>(key: K, merged: Record<string, unknown>): (typeof DEFAULTS)[K] {
  if (key === "processing") {
    num(merged.target_clips_max, 1, 100, "target_clips_max");
    num(merged.min_views, 0, 100000, "min_views");
    num(merged.min_clip_s, 5, 60, "min_clip_s");
    num(merged.max_clip_s, 5, 60, "max_clip_s");
    num(merged.zoom_intensity, 0, 1.5, "zoom_intensity");
    if ((merged.min_clip_s as number) >= (merged.max_clip_s as number)) throw new Error("min_clip_s must be < max_clip_s");
    if (!["fit", "crop"].includes(String(merged.default_layout))) throw new Error("default_layout must be fit or crop");
    merged.auto_process = Boolean(merged.auto_process);
  } else {
    if (!["inbox_draft", "direct_post"].includes(String(merged.auto_mode))) throw new Error("invalid auto_mode");
    num(merged.max_posts_per_day, 1, 15, "max_posts_per_day");
    num(merged.cooldown_minutes, 10, 1440, "cooldown_minutes");
    for (const b of ["auto_publish", "client_audited"]) merged[b] = Boolean(merged[b]);
  }
  const allowed = Object.keys(DEFAULTS[key]);
  return Object.fromEntries(Object.entries(merged).filter(([k]) => allowed.includes(k))) as unknown as (typeof DEFAULTS)[K];
}

export async function updateSection<K extends Section>(db: D1Database, key: K, patch: Record<string, unknown>,
                                                       actor: string): Promise<(typeof DEFAULTS)[K]> {
  const merged = { ...(await getSection(db, key)), ...patch } as Record<string, unknown>;
  const value = validate(key, merged);
  await run(db, `INSERT INTO settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, updated_at = excluded.updated_at`,
    key, s(value), actor, now());
  return value;
}
