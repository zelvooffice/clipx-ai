/// <reference types="@cloudflare/workers-types" />
/**
 * Runs every minute (Workers Cron Trigger, free plan). Each step is idempotent and bounded so one invocation stays
 * well inside the free plan limits (10 ms CPU, 50 subrequests): network waits do not count as CPU time.
 */
import { event } from "./audit";
import { all, now, one, run, s, uuid } from "./db";
import type { Env } from "./env";
import { ensureSchema } from "./schema";
import { getSection } from "./settings";
import * as tiktok from "./tiktok";
import * as twitch from "./twitch";

export interface CronReport { [step: string]: unknown }

export async function runScheduled(env: Env, minute = Math.floor(Date.now() / 60000)): Promise<CronReport> {
  await ensureSchema(env.DB);
  const report: CronReport = {};
  const step = async (name: string, fn: () => Promise<unknown>) => {
    try {
      report[name] = await fn();
    } catch (e) {
      report[name] = { error: String(e instanceof Error ? e.message : e) };
      await event(env.DB, "ERROR", "cron", name, `${name} failed: ${String(e instanceof Error ? e.message : e)}`.slice(0, 500));
    }
  };
  if (twitch.configured(env)) {
    await step("live", () => detectLive(env));
    await step("clips", () => syncClips(env));
    await step("media", () => fetchMedia(env, 3));
    if (minute % 360 === 11) await step("viral", () => discoverViral(env));
  }
  await step("claims", () => run(env.DB, "UPDATE clips SET status = 'media_ready', claimed_until = NULL, status_detail = 'browser processing timed out; re-queued' WHERE status = 'processing' AND claimed_until < ?", now()));
  if (tiktok.configured(env)) {
    await step("publishing", () => runPublishing(env));
    if (minute % 180 === 23) await step("analytics", () => syncAnalytics(env));
  }
  await run(env.DB, "INSERT INTO cache (key, value, expires_at) VALUES ('cron:last', ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at",
    s({ at: now(), report }), now() + 86400);
  return report;
}

// --------------------------------------------------------------------------------------- live detection
export async function detectLive(env: Env) {
  const tgt = await twitch.target(env);
  const live = await twitch.liveStream(env, tgt.id);
  const current = await one<{ id: string }>(env.DB, "SELECT id FROM streams WHERE status = 'live' ORDER BY started_at DESC LIMIT 1");
  if (live && current?.id !== live.id) {
    if (current) await endStream(env, current.id, "superseded by a new stream");
    await run(env.DB, `INSERT INTO streams (id, broadcaster_id, title, game, started_at, status, detected_via)
      VALUES (?, ?, ?, ?, ?, 'live', 'cron') ON CONFLICT(id) DO UPDATE SET status = 'live', ended_at = NULL`,
      live.id, tgt.id, live.title, live.game_name, Math.floor(Date.parse(live.started_at) / 1000));
    await event(env.DB, "INFO", "live", "stream_online", `Lacy is live: ${live.title}`, live.id);
    return { state: "went_live", stream: live.id };
  }
  if (!live && current) {
    await endStream(env, current.id, "stream.offline");
    return { state: "went_offline", stream: current.id };
  }
  if (live) await run(env.DB, "UPDATE streams SET title = ?, game = ? WHERE id = ?", live.title, live.game_name, live.id);
  return { state: live ? "live" : "offline" };
}

async function endStream(env: Env, id: string, reason: string) {
  await run(env.DB, "UPDATE streams SET status = 'ended', ended_at = ? WHERE id = ?", now(), id);
  await event(env.DB, "INFO", "live", "stream_offline", `Stream ended (${reason}); collecting viewer clips for the next 48 h`, id);
}

// --------------------------------------------------------------------------------------- viewer clips
interface StreamRow { id: string; broadcaster_id: string; started_at: number; ended_at: number | null; status: string;
  clips_synced_at: number | null; title: string | null; game: string | null }

function syncInterval(st: StreamRow): number {
  if (st.status === "live") return 300;
  const since = now() - (st.ended_at ?? now());
  return since < 3600 ? 600 : since < 6 * 3600 ? 3600 : 6 * 3600;
}

export async function syncClips(env: Env) {
  const candidates = await all<StreamRow>(env.DB, `SELECT * FROM streams WHERE status = 'live' OR ended_at > ? ORDER BY started_at DESC LIMIT 5`,
    now() - 48 * 3600);
  const due = candidates.find((st) => !st.clips_synced_at || now() - st.clips_synced_at >= syncInterval(st));
  if (!due) return { synced: null };
  const clips = await twitch.clipsBetween(env, due.broadcaster_id, due.started_at - 60, due.ended_at ?? now() + 60);
  for (const c of clips) {
    const created = Math.floor(Date.parse(c.created_at) / 1000);
    await run(env.DB, `INSERT INTO clips (id, origin, stream_id, twitch_clip_id, twitch_url, title, clipper, view_count, twitch_created_at,
        vod_offset, duration, status, category, created_at, updated_at)
      VALUES (?, 'twitch_clip', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', 'moment', ?, ?)
      ON CONFLICT(twitch_clip_id) DO UPDATE SET view_count = excluded.view_count, title = excluded.title, updated_at = excluded.updated_at`,
      uuid(), due.id, c.id, c.url, c.title || "Lacy moment", c.creator_name, c.view_count, created, c.vod_offset, c.duration, now(), now());
  }
  const selection = await rankAndSelect(env, due);
  await run(env.DB, "UPDATE streams SET clips_synced_at = ?, sync_count = sync_count + 1, stats = ? WHERE id = ?",
    now(), s({ viewer_clips: clips.length, ...selection }), due.id);
  return { stream: due.id, viewer_clips: clips.length, ...selection };
}

interface ClipRank { id: string; view_count: number; vod_offset: number | null; duration: number; twitch_created_at: number; status: string }

/** Rank by views, drop overlapping moments (duplicates), select the best N above the view threshold. Never padded. */
export async function rankAndSelect(env: Env, st: StreamRow) {
  const cfg = await getSection(env.DB, "processing");
  const rows = await all<ClipRank>(env.DB, "SELECT id, view_count, vod_offset, duration, twitch_created_at, status FROM clips WHERE stream_id = ? AND origin = 'twitch_clip'", st.id);
  const maxViews = Math.max(1, ...rows.map((r) => r.view_count ?? 0));
  const startOf = (r: ClipRank) => r.vod_offset ?? (r.twitch_created_at - st.started_at - (r.duration ?? 30));
  const kept: { start: number; end: number }[] = [];
  let selected = 0, duplicates = 0, below = 0;
  const locked = new Set(["media_ready", "processing", "rendered", "rejected", "failed"]);
  for (const r of [...rows].sort((a, b) => (b.view_count ?? 0) - (a.view_count ?? 0))) {
    const start = startOf(r), end = start + (r.duration ?? 30);
    const overlap = kept.some((k) => {
      const inter = Math.max(0, Math.min(end, k.end) - Math.max(start, k.start));
      const union = Math.max(end, k.end) - Math.min(start, k.start);
      return union > 0 && inter / union > 0.3;
    });
    const score = Math.round(((r.view_count ?? 0) / maxViews) * 1000) / 1000;
    let status: string, sel = 0;
    if (overlap) { status = "duplicate"; duplicates++; }
    else if ((r.view_count ?? 0) < cfg.min_views || (r.duration ?? 0) < Math.min(cfg.min_clip_s, 5)) { status = "discovered"; below++; }
    else if (selected >= cfg.target_clips_max) { status = "discovered"; }
    else { status = "needs_media"; sel = 1; selected++; kept.push({ start, end }); }
    if (locked.has(r.status)) {
      if (sel === 0 && r.status !== "rejected") kept.push({ start, end });
      await run(env.DB, "UPDATE clips SET score = ? WHERE id = ?", score, r.id);
      continue;
    }
    await run(env.DB, "UPDATE clips SET score = ?, selected = ?, status = ?, updated_at = ? WHERE id = ?", score, sel, status, now(), r.id);
  }
  return { selected, duplicates, below_threshold: below };
}

// --------------------------------------------------------------------------------------- authorized media
export async function fetchMedia(env: Env, limit: number) {
  const pending = await all<{ id: string; twitch_clip_id: string; stream_id: string }>(env.DB,
    `SELECT c.id, c.twitch_clip_id, c.stream_id FROM clips c WHERE c.status = 'needs_media' AND c.selected = 1
     ORDER BY c.score DESC LIMIT ?`, limit);
  if (!pending.length) return { fetched: 0 };
  const acct = await twitch.mediaAccount(env);
  if (!acct) {
    await run(env.DB, `UPDATE clips SET status_detail = 'Waiting for Lacy or a channel editor to authorize clip downloads' WHERE status = 'needs_media'`);
    return { fetched: 0, blocked: "no editor/broadcaster authorization" };
  }
  const tgt = await twitch.target(env);
  const token = await twitch.userToken(env, acct.user_id);
  let data: { clip_id: string; landscape_download_url: string | null; portrait_download_url: string | null }[];
  try {
    ({ body: { data } } = await twitch.helix<{ data: typeof data }>(env, token, "/clips/downloads",
      [["editor_id", acct.user_id], ["broadcaster_id", tgt.id], ...pending.map((p) => ["clip_id", p.twitch_clip_id] as [string, string])]));
  } catch (e) {
    if (e instanceof twitch.TwitchError && e.status === 403) {
      await run(env.DB, "UPDATE twitch_accounts SET editor_verified = 0 WHERE user_id = ?", acct.user_id);
      await event(env.DB, "WARNING", "twitch", "not_editor", `${acct.login} is not an editor of the channel; clip media unavailable`);
      return { fetched: 0, blocked: "account is not an editor" };
    }
    throw e;
  }
  if (acct.editor_verified === null) await run(env.DB, "UPDATE twitch_accounts SET editor_verified = 1 WHERE user_id = ?", acct.user_id);
  let fetched = 0;
  for (const p of pending) {
    const url = data.find((d) => d.clip_id === p.twitch_clip_id);
    const src = url?.landscape_download_url ?? url?.portrait_download_url;
    if (!src) {
      await run(env.DB, "UPDATE clips SET status_detail = 'Twitch has not provided a download URL yet; retrying' WHERE id = ?", p.id);
      continue;
    }
    const r = await fetch(src);
    const len = Number(r.headers.get("content-length") ?? 0);
    if (!r.ok || !r.body || !len) {
      await run(env.DB, "UPDATE clips SET status_detail = ? WHERE id = ?", `download failed (${r.status}); retrying`, p.id);
      continue;
    }
    const key = `clips/${p.id}/source.mp4`;
    const { readable, writable } = new FixedLengthStream(len);
    r.body.pipeTo(writable);
    await env.MEDIA.put(key, readable, { httpMetadata: { contentType: "video/mp4" } });
    await run(env.DB, `UPDATE clips SET status = 'media_ready', media_key = ?, status_detail = NULL,
      rights_basis = ?, updated_at = ? WHERE id = ?`, key, `Twitch Get Clips Download as ${acct.role} '${acct.login}'`, now(), p.id);
    fetched++;
  }
  if (fetched) await event(env.DB, "INFO", "media", "fetched", `Downloaded ${fetched} clip(s) through Get Clips Download; ready for editing`);
  return { fetched };
}

// --------------------------------------------------------------------------------------- publishing
export async function runPublishing(env: Env) {
  const cfg = await getSection(env.DB, "publishing");
  const acct = await tiktok.account(env);
  if (!acct) return { skipped: "no TikTok account" };
  const out: Record<string, unknown> = {};
  const due = await one<tiktok.JobRow>(env.DB, `SELECT * FROM publishing_jobs WHERE status = 'scheduled'
    AND (scheduled_for IS NULL OR scheduled_for <= ?) ORDER BY scheduled_for LIMIT 1`, now());
  if (due) {
    const cap = await tiktok.capacity(env, cfg.max_posts_per_day, cfg.cooldown_minutes, acct.open_id);
    if (due.mode === "inbox_draft" && !due.confirmed_by && !(cfg.auto_publish && cfg.automation_consent_at)) {
      await run(env.DB, "UPDATE publishing_jobs SET status = 'cancelled', updated_at = ? WHERE id = ?", now(), due.id);
      await tiktok.history(env, due.id, "cancelled", "cancelled", { reason: "auto-publish disabled before upload" });
      out.cancelled = due.id;
    } else if (cap.remaining_today <= 0 || cap.next_allowed_at > now() + 30) {
      await run(env.DB, "UPDATE publishing_jobs SET scheduled_for = ? WHERE id = ?", Math.max(cap.next_allowed_at, now() + 3600), due.id);
      out.deferred = due.id;
    } else {
      await tiktok.execute(env, due);
      out.executed = due.id;
    }
  }
  const processing = await all<tiktok.JobRow>(env.DB, "SELECT * FROM publishing_jobs WHERE status = 'processing' ORDER BY updated_at LIMIT 3");
  for (const j of processing) await tiktok.pollStatus(env, j);
  out.polled = processing.length;
  return out;
}

export async function syncAnalytics(env: Env) {
  const acct = await tiktok.account(env);
  if (!acct) return { skipped: "no TikTok account" };
  const tok = await tiktok.accessToken(env, acct.open_id);
  const jobs = await all<{ id: string; clip_id: string; post_ids: string }>(env.DB, "SELECT id, clip_id, post_ids FROM publishing_jobs WHERE status = 'published'");
  const byPost = new Map<string, string>();
  for (const jb of jobs) for (const pid of JSON.parse(jb.post_ids || "[]") as string[]) byPost.set(pid, jb.clip_id);
  let n = 0;
  let cursor: number | undefined;
  for (let page = 0; page < 5; page++) {
    const d = await tiktok.call<{ videos?: Record<string, unknown>[]; cursor?: number; has_more?: boolean }>(tok, "POST", "/v2/video/list/",
      cursor ? { max_count: 20, cursor } : { max_count: 20 },
      { fields: "id,create_time,title,video_description,duration,share_url,view_count,like_count,comment_count,share_count" });
    for (const v of d.videos ?? []) {
      await run(env.DB, `INSERT INTO analytics (video_id, clip_id, title, posted_at, duration, views, likes, comments, shares, share_url, captured_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, String(v.id), byPost.get(String(v.id)) ?? null, (v.title ?? v.video_description ?? null) as string | null,
        (v.create_time as number) ?? null, (v.duration as number) ?? null, (v.view_count as number) ?? null, (v.like_count as number) ?? null,
        (v.comment_count as number) ?? null, (v.share_count as number) ?? null, (v.share_url as string) ?? null, now());
      n++;
    }
    if (!d.has_more) break;
    cursor = d.cursor;
  }
  return { videos: n };
}

// --------------------------------------------------------------------------------------- viral discovery
export async function discoverViral(env: Env) {
  const tgt = await twitch.target(env);
  const media = Boolean(await twitch.mediaAccount(env));
  const clips = await twitch.clipsBetween(env, tgt.id, now() - 30 * 86400, now(), 2);
  for (const c of clips) {
    const pub = Math.floor(Date.parse(c.created_at) / 1000);
    const age = Math.max(0.5, (now() - pub) / 86400);
    const pop = Math.round(Math.log10(1 + c.view_count / Math.pow(age, 0.8)) * 10000) / 10000;
    await run(env.DB, `INSERT INTO viral_sources (id, platform, url, title, creator, published_at, view_count, duration, popularity, rights, fetched_at)
      VALUES (?, 'twitch', ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET view_count = excluded.view_count,
      popularity = excluded.popularity, rights = excluded.rights, fetched_at = excluded.fetched_at`,
      `twitch:${c.id}`, c.url, c.title, c.creator_name, pub, c.view_count, c.duration, pop, media ? "authorized" : "metadata_only", now());
  }
  return { sources: clips.length };
}
