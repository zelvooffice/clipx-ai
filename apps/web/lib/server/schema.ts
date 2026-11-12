/// <reference types="@cloudflare/workers-types" />
/**
 * D1 schema. The Worker migrates itself (idempotent, versioned) on first use per isolate, so a deploy never
 * needs a manual migration step.
 */
const MIGRATIONS: string[] = [
  `CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by TEXT, updated_at INTEGER);
   CREATE TABLE IF NOT EXISTS authorized_emails (email TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1, added_by TEXT, created_at INTEGER);
   CREATE TABLE IF NOT EXISTS unlock_sessions (sid TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS unlock_failures (email TEXT PRIMARY KEY, count INTEGER NOT NULL, window_start INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT, detail TEXT, ip TEXT);
   CREATE INDEX IF NOT EXISTS ix_audit_at ON audit_logs(at);
   CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, level TEXT NOT NULL, component TEXT NOT NULL, event TEXT NOT NULL, message TEXT NOT NULL, stream_id TEXT);
   CREATE INDEX IF NOT EXISTS ix_events_at ON events(at);
   CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS oauth_states (state TEXT PRIMARY KEY, provider TEXT NOT NULL, role TEXT, created_by TEXT NOT NULL, expires_at INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS tokens (id TEXT PRIMARY KEY, provider TEXT NOT NULL, subject TEXT NOT NULL, access_enc TEXT NOT NULL, refresh_enc TEXT, scopes TEXT NOT NULL DEFAULT '[]', expires_at INTEGER, refresh_expires_at INTEGER, validated_at INTEGER, revoked INTEGER NOT NULL DEFAULT 0, updated_at INTEGER);
   CREATE TABLE IF NOT EXISTS twitch_accounts (user_id TEXT PRIMARY KEY, login TEXT NOT NULL, role TEXT NOT NULL, editor_verified INTEGER, connected_by TEXT, created_at INTEGER);
   CREATE TABLE IF NOT EXISTS tiktok_accounts (open_id TEXT PRIMARY KEY, display_name TEXT, username TEXT, avatar_url TEXT, creator_info TEXT, creator_info_at INTEGER, connected_by TEXT, created_at INTEGER);
   CREATE TABLE IF NOT EXISTS streams (id TEXT PRIMARY KEY, broadcaster_id TEXT NOT NULL, title TEXT, game TEXT, started_at INTEGER NOT NULL, ended_at INTEGER, status TEXT NOT NULL, detected_via TEXT, clips_synced_at INTEGER, sync_count INTEGER NOT NULL DEFAULT 0, stats TEXT NOT NULL DEFAULT '{}');
   CREATE INDEX IF NOT EXISTS ix_streams_status ON streams(status, started_at);
   CREATE TABLE IF NOT EXISTS clips (
     id TEXT PRIMARY KEY, origin TEXT NOT NULL DEFAULT 'twitch_clip', stream_id TEXT, twitch_clip_id TEXT UNIQUE, twitch_url TEXT,
     title TEXT NOT NULL, clipper TEXT, view_count INTEGER, twitch_created_at INTEGER, vod_offset INTEGER, duration REAL,
     score REAL, selected INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, status_detail TEXT,
     media_key TEXT, render_key TEXT, thumb_key TEXT, render_version INTEGER NOT NULL DEFAULT 0, rights_basis TEXT,
     words TEXT, cues TEXT, cues_edited INTEGER NOT NULL DEFAULT 0, edit TEXT NOT NULL DEFAULT '{}', transcript TEXT,
     caption TEXT, hashtags TEXT NOT NULL DEFAULT '[]', caption_source TEXT, category TEXT NOT NULL DEFAULT 'moment',
     claimed_until INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
   CREATE INDEX IF NOT EXISTS ix_clips_status ON clips(status, updated_at);
   CREATE INDEX IF NOT EXISTS ix_clips_stream ON clips(stream_id, score);
   CREATE TABLE IF NOT EXISTS publishing_jobs (
     id TEXT PRIMARY KEY, clip_id TEXT NOT NULL, account_id TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
     privacy_level TEXT, allow_comment INTEGER NOT NULL DEFAULT 0, allow_duet INTEGER NOT NULL DEFAULT 0, allow_stitch INTEGER NOT NULL DEFAULT 0,
     brand_content INTEGER NOT NULL DEFAULT 0, brand_organic INTEGER NOT NULL DEFAULT 0, consent_at INTEGER, confirmed_by TEXT,
     scheduled_for INTEGER, schedule_basis TEXT, transfer TEXT, publish_id TEXT, post_ids TEXT NOT NULL DEFAULT '[]', fail_reason TEXT,
     attempts INTEGER NOT NULL DEFAULT 0, published_at INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
   CREATE UNIQUE INDEX IF NOT EXISTS uq_publish_active ON publishing_jobs(clip_id, account_id) WHERE status NOT IN ('failed','cancelled');
   CREATE INDEX IF NOT EXISTS ix_publish_status ON publishing_jobs(status, scheduled_for);
   CREATE TABLE IF NOT EXISTS publishing_history (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, event TEXT NOT NULL, status TEXT, detail TEXT, at INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS analytics (id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT NOT NULL, clip_id TEXT, title TEXT, posted_at INTEGER, duration REAL, views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, share_url TEXT, captured_at INTEGER NOT NULL);
   CREATE INDEX IF NOT EXISTS ix_analytics_video ON analytics(video_id, captured_at);
   CREATE TABLE IF NOT EXISTS viral_sources (id TEXT PRIMARY KEY, platform TEXT NOT NULL, url TEXT NOT NULL, title TEXT, creator TEXT, published_at INTEGER, view_count INTEGER, duration REAL, popularity REAL NOT NULL DEFAULT 0, rights TEXT NOT NULL, fetched_at INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS viral_edits (id TEXT PRIMARY KEY, template TEXT NOT NULL, text TEXT, input_clip_ids TEXT NOT NULL, output_clip_id TEXT, status TEXT NOT NULL, created_by TEXT, created_at INTEGER NOT NULL);`,
];

let migrated = false;

export async function ensureSchema(db: D1Database): Promise<void> {
  if (migrated) return;
  await db.exec("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)");
  const row = await db.prepare("SELECT MAX(version) AS v FROM schema_version").first<{ v: number | null }>();
  const current = row?.v ?? 0;
  for (let i = current; i < MIGRATIONS.length; i++) {
    const statements = MIGRATIONS[i].split(/;\s*\n/).map((x) => x.trim()).filter(Boolean);
    await db.batch([
      ...statements.map((st) => db.prepare(st)),
      db.prepare("INSERT INTO schema_version (version) VALUES (?)").bind(i + 1),
    ]);
  }
  migrated = true;
}

export function resetSchemaFlag() {
  migrated = false;
}
