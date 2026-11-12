/// <reference types="@cloudflare/workers-types" />
/**
 * TikTok Login Kit + Content Posting API (verified against developers.tiktok.com, 2026-11-11).
 * Refresh tokens rotate on every refresh (always stored). Direct Post requires the creator's explicit per-post
 * choices (privacy without default, interactions off by default, consent text) — never posted silently.
 * Media is streamed from R2 straight to TikTok's upload URL (single chunk <= 64 MB, per the media transfer rules).
 */
import { randomToken } from "./crypto";
import { all, j, now, one, run, s, uuid } from "./db";
import type { Env } from "./env";
import { consumeState, readToken, storeToken, type TokenSet } from "./twitch";

const API = "https://open.tiktokapis.com";
const JSON_CT = "application/json; charset=UTF-8";
export const SCOPES = "user.info.basic,video.publish,video.upload,video.list";
export const CONSENT_TEXT = "By posting, you agree to TikTok's Music Usage Confirmation";
export const CONSENT_TEXT_BRANDED = "By posting, you agree to TikTok's Branded Content Policy and Music Usage Confirmation";
export const INBOX_AUTOMATION_CONSENT =
  "I allow CLIPX AI to automatically send eligible rendered clips as drafts to this TikTok account's inbox. Nothing is posted until I finish each post in the TikTok app.";

export class TikTokError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(`TikTok ${status} ${code}: ${message}`);
  }
  get retryable() {
    return this.status === 429 || this.status >= 500 || this.code === "rate_limit_exceeded" || this.code === "internal_error";
  }
}

export const configured = (env: Env) => Boolean(env.TIKTOK_CLIENT_KEY && env.TIKTOK_CLIENT_SECRET);

export async function startConnect(env: Env, email: string, redirectUri: string): Promise<string> {
  if (!configured(env)) throw new Error("TikTok app credentials are not configured");
  const state = randomToken(32);
  await run(env.DB, "INSERT INTO oauth_states (state, provider, role, created_by, expires_at) VALUES (?, 'tiktok', NULL, ?, ?)",
    state, email, now() + 600);
  const q = new URLSearchParams({ client_key: env.TIKTOK_CLIENT_KEY ?? "", scope: SCOPES, response_type: "code",
    redirect_uri: redirectUri, state });
  return `https://www.tiktok.com/v2/auth/authorize/?${q}`;
}

async function token(env: Env, body: Record<string, string>) {
  const r = await fetch(`${API}/v2/oauth/token/`, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_key: env.TIKTOK_CLIENT_KEY ?? "", client_secret: env.TIKTOK_CLIENT_SECRET ?? "", ...body }) });
  const d = (await r.json().catch(() => ({}))) as Record<string, unknown>;
  if (!r.ok || !d.access_token) throw new TikTokError(r.status, String(d.error ?? "token_error"), String(d.error_description ?? ""));
  return { set: { access_token: String(d.access_token), refresh_token: (d.refresh_token as string) ?? null,
                  expires_in: (d.expires_in as number) ?? null, scopes: String(d.scope ?? "").split(",").filter(Boolean) } as TokenSet,
           openId: String(d.open_id), refreshExpiresIn: (d.refresh_expires_in as number) ?? null };
}

export async function call<T = Record<string, unknown>>(accessToken: string, method: string, path: string,
                                                        body?: unknown, query?: Record<string, string>): Promise<T> {
  const url = `${API}${path}${query ? `?${new URLSearchParams(query)}` : ""}`;
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await fetch(url, { method, headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": JSON_CT },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const d = (await r.json().catch(() => ({}))) as { data?: T; error?: { code?: string; message?: string } };
    const code = d.error?.code ?? (r.ok ? "ok" : "http_error");
    if (r.ok && code === "ok") return (d.data ?? {}) as T;
    const err = new TikTokError(r.status, code, d.error?.message ?? r.statusText);
    if (err.retryable && attempt === 0) continue;
    throw err;
  }
  throw new TikTokError(503, "unreachable", "");
}

export async function completeConnect(env: Env, code: string, state: string, email: string, redirectUri: string) {
  await consumeState(env, "tiktok", state, email);
  const { set, openId, refreshExpiresIn } = await token(env, { code, grant_type: "authorization_code", redirect_uri: redirectUri });
  const user = await call<{ user?: { display_name?: string; avatar_url?: string } }>(set.access_token, "GET", "/v2/user/info/",
    undefined, { fields: "open_id,avatar_url,display_name" });
  let creator: Record<string, unknown> | null = null;
  if (set.scopes.includes("video.publish")) creator = await call(set.access_token, "POST", "/v2/post/publish/creator_info/query/", {});
  await storeToken(env, "tiktok", openId, set, refreshExpiresIn);
  await run(env.DB, `INSERT INTO tiktok_accounts (open_id, display_name, username, avatar_url, creator_info, creator_info_at, connected_by, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(open_id) DO UPDATE SET display_name = excluded.display_name, username = excluded.username,
    avatar_url = excluded.avatar_url, creator_info = excluded.creator_info, creator_info_at = excluded.creator_info_at, connected_by = excluded.connected_by`,
    openId, user.user?.display_name ?? null, (creator?.creator_username as string) ?? null, user.user?.avatar_url ?? null,
    creator ? s(creator) : null, creator ? now() : null, email, now());
  return { open_id: openId, display_name: user.user?.display_name ?? null };
}

export interface TikTokAccount { open_id: string; display_name: string | null; username: string | null; avatar_url: string | null;
  creator_info: string | null; creator_info_at: number | null; connected_by: string | null }

export const account = (env: Env) =>
  one<TikTokAccount>(env.DB, "SELECT * FROM tiktok_accounts ORDER BY created_at DESC LIMIT 1");

export async function accessToken(env: Env, openId: string): Promise<string> {
  const id = `tiktok:${openId}`;
  const t = await readToken(env, id);
  if (!t) throw new Error("TikTok account needs to be reconnected");
  if (t.row.expires_at !== null && t.row.expires_at - now() < 300) {
    if (!t.row.refresh_enc) throw new Error("TikTok token expired; reconnect");
    const { decrypt } = await import("./crypto");
    try {
      const { set, refreshExpiresIn } = await token(env, { grant_type: "refresh_token",
        refresh_token: await decrypt(env.TOKEN_ENCRYPTION_KEY ?? "", t.row.refresh_enc, id) });
      await storeToken(env, "tiktok", openId, set, refreshExpiresIn); // rotated refresh token stored
      return set.access_token;
    } catch (e) {
      await run(env.DB, "UPDATE tokens SET revoked = 1 WHERE id = ?", id);
      throw e;
    }
  }
  return t.access;
}

export async function refreshCreatorInfo(env: Env, acct: TikTokAccount) {
  const info = await call<Record<string, unknown>>(await accessToken(env, acct.open_id), "POST", "/v2/post/publish/creator_info/query/", {});
  await run(env.DB, "UPDATE tiktok_accounts SET creator_info = ?, creator_info_at = ?, username = COALESCE(?, username) WHERE open_id = ?",
    s(info), now(), (info.creator_username as string) ?? null, acct.open_id);
  return info;
}

// ------------------------------------------------------------------------------------- policy
export function consentText(brandContent: boolean) {
  return brandContent ? CONSENT_TEXT_BRANDED : CONSENT_TEXT;
}

export interface Choices { privacy_level: string | null; allow_comment: boolean; allow_duet: boolean; allow_stitch: boolean;
  consent: boolean; duration_s: number; brand_content: boolean; brand_organic: boolean; disclosure_on: boolean; client_audited: boolean }

/** Server-side enforcement of TikTok's required UX for one Direct Post. */
export function validateChoices(ci: Record<string, unknown>, c: Choices): string[] {
  const errors: string[] = [];
  const options = (ci.privacy_level_options as string[]) ?? [];
  if (!c.privacy_level) errors.push("privacy level must be selected manually");
  else if (!options.includes(c.privacy_level)) errors.push("privacy level is not one of the creator's available options");
  else if (!c.client_audited && c.privacy_level !== "SELF_ONLY")
    errors.push("this TikTok app is not audited yet: TikTok only allows SELF_ONLY (private) posts");
  if (c.allow_comment && ci.comment_disabled) errors.push("comments are disabled in the creator's TikTok settings");
  if (c.allow_duet && ci.duet_disabled) errors.push("duets are disabled in the creator's TikTok settings");
  if (c.allow_stitch && ci.stitch_disabled) errors.push("stitches are disabled in the creator's TikTok settings");
  const maxD = ci.max_video_post_duration_sec as number | undefined;
  if (maxD && c.duration_s > maxD) errors.push(`video is ${Math.round(c.duration_s)}s; this creator can post at most ${maxD}s`);
  if (c.disclosure_on && !c.brand_content && !c.brand_organic)
    errors.push("You need to indicate if your content promotes yourself, a third party, or both.");
  if (c.brand_content && c.privacy_level === "SELF_ONLY") errors.push("Branded content visibility cannot be set to private.");
  if (!c.consent) errors.push(`consent is required: '${consentText(c.brand_content)}'`);
  return errors;
}

export async function history(env: Env, jobId: string, event: string, status: string, detail: unknown = {}) {
  await run(env.DB, "INSERT INTO publishing_history (job_id, event, status, detail, at) VALUES (?, ?, ?, ?, ?)",
    jobId, event, status, s(detail), now());
}

export const ACTIVE = ["awaiting_confirmation", "scheduled", "uploading", "processing", "sent_to_inbox", "published"];

export async function capacity(env: Env, maxPerDay: number, cooldownMin: number, accountId: string) {
  const rows = await all<{ t: number }>(env.DB, `SELECT COALESCE(published_at, updated_at) AS t FROM publishing_jobs
    WHERE account_id = ? AND status IN ('published','processing','uploading','sent_to_inbox') AND updated_at >= ?`,
    accountId, now() - 86400);
  const last = rows.reduce((m, r) => Math.max(m, r.t), 0);
  return { posts_24h: rows.length, max_per_day: maxPerDay, remaining_today: Math.max(0, maxPerDay - rows.length),
           next_allowed_at: Math.max(now(), last ? last + cooldownMin * 60 : now()) };
}

/** Create the job for a rendered clip. Direct Post always waits for the compliant confirmation. */
export async function prepare(env: Env, clipId: string, mode: "direct_post" | "inbox_draft", scheduledFor: number | null,
                              basis: string | null, confirmedBy: string | null = null): Promise<string | null> {
  const acct = await account(env);
  if (!acct) return null;
  const clip = await one<{ caption: string | null; hashtags: string; status: string }>(env.DB,
    "SELECT caption, hashtags, status FROM clips WHERE id = ?", clipId);
  if (!clip || clip.status !== "rendered") return null;
  const existing = await one<{ id: string }>(env.DB, `SELECT id FROM publishing_jobs WHERE clip_id = ? AND account_id = ?
    AND status NOT IN ('failed','cancelled')`, clipId, acct.open_id);
  if (existing) return existing.id;
  const id = uuid();
  const status = mode === "direct_post" ? "awaiting_confirmation" : "scheduled";
  const title = `${clip.caption ?? ""} ${j<string[]>(clip.hashtags, []).join(" ")}`.trim().slice(0, 2200);
  await run(env.DB, `INSERT INTO publishing_jobs (id, clip_id, account_id, mode, status, title, scheduled_for, schedule_basis,
    confirmed_by, consent_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    id, clipId, acct.open_id, mode, status, title, scheduledFor, basis, confirmedBy, confirmedBy ? now() : null, now(), now());
  await history(env, id, "prepared", status, { mode });
  return id;
}

interface JobRow { id: string; clip_id: string; account_id: string; mode: string; status: string; title: string;
  privacy_level: string | null; allow_comment: number; allow_duet: number; allow_stitch: number; brand_content: number;
  brand_organic: number; confirmed_by: string | null; publish_id: string | null; attempts: number }

/** Upload one scheduled job (called from the cron). */
export async function execute(env: Env, job: JobRow): Promise<void> {
  const clip = await one<{ render_key: string | null }>(env.DB, "SELECT render_key FROM clips WHERE id = ?", job.clip_id);
  const obj = clip?.render_key ? await env.MEDIA.get(clip.render_key) : null;
  if (!obj) {
    await run(env.DB, "UPDATE publishing_jobs SET status = 'failed', fail_reason = 'rendered video missing', updated_at = ? WHERE id = ?", now(), job.id);
    return;
  }
  if (obj.size > 64 * 1024 * 1024) {
    await run(env.DB, "UPDATE publishing_jobs SET status = 'failed', fail_reason = 'video larger than 64 MB', updated_at = ? WHERE id = ?", now(), job.id);
    return;
  }
  const tok = await accessToken(env, job.account_id);
  const source = { source: "FILE_UPLOAD", video_size: obj.size, chunk_size: obj.size, total_chunk_count: 1 };
  await run(env.DB, "UPDATE publishing_jobs SET status = 'uploading', transfer = 'FILE_UPLOAD', attempts = attempts + 1, updated_at = ? WHERE id = ?", now(), job.id);
  try {
    let init: { publish_id: string; upload_url: string };
    if (job.mode === "direct_post") {
      const ci = await call<Record<string, unknown>>(tok, "POST", "/v2/post/publish/creator_info/query/", {});
      if (!((ci.privacy_level_options as string[]) ?? []).includes(job.privacy_level ?? "")) {
        throw new TikTokError(400, "privacy_level_option_mismatch", "privacy level no longer available; re-confirm");
      }
      init = await call(tok, "POST", "/v2/post/publish/video/init/", {
        post_info: { privacy_level: job.privacy_level, title: job.title, disable_comment: !job.allow_comment,
          disable_duet: !job.allow_duet, disable_stitch: !job.allow_stitch, brand_content_toggle: Boolean(job.brand_content),
          brand_organic_toggle: Boolean(job.brand_organic), is_aigc: false, video_cover_timestamp_ms: 1000 },
        source_info: source });
    } else {
      init = await call(tok, "POST", "/v2/post/publish/inbox/video/init/", { source_info: source });
    }
    await run(env.DB, "UPDATE publishing_jobs SET publish_id = ?, updated_at = ? WHERE id = ?", init.publish_id, now(), job.id);
    await history(env, job.id, "initialized", "uploading", { publish_id: init.publish_id });
    const { readable, writable } = new FixedLengthStream(obj.size);
    obj.body.pipeTo(writable);
    const put = await fetch(init.upload_url, { method: "PUT", body: readable, headers: {
      "Content-Type": "video/mp4", "Content-Range": `bytes 0-${obj.size - 1}/${obj.size}` } });
    if (put.status !== 201 && put.status !== 206) throw new TikTokError(put.status, "upload_failed", await put.text());
    await run(env.DB, "UPDATE publishing_jobs SET status = 'processing', updated_at = ? WHERE id = ?", now(), job.id);
    await history(env, job.id, "uploaded", "processing", { bytes: obj.size });
  } catch (e) {
    const err = e instanceof TikTokError ? e : new TikTokError(0, "error", String(e));
    const retry = err.retryable && job.attempts < 3;
    await run(env.DB, "UPDATE publishing_jobs SET status = ?, fail_reason = ?, scheduled_for = ?, updated_at = ? WHERE id = ?",
      retry ? "scheduled" : "failed", `${err.code}: ${err.message}`.slice(0, 1000), retry ? now() + 300 : null, now(), job.id);
    await history(env, job.id, "error", retry ? "scheduled" : "failed", { code: err.code, message: err.message });
  }
}

export async function pollStatus(env: Env, job: JobRow): Promise<void> {
  if (!job.publish_id) return;
  const d = await call<{ status?: string; fail_reason?: string; publicaly_available_post_id?: number[] }>(
    await accessToken(env, job.account_id), "POST", "/v2/post/publish/status/fetch/", { publish_id: job.publish_id });
  if (d.status === "PUBLISH_COMPLETE") {
    await run(env.DB, "UPDATE publishing_jobs SET status = 'published', published_at = ?, post_ids = ?, updated_at = ? WHERE id = ?",
      now(), s((d.publicaly_available_post_id ?? []).map(String)), now(), job.id);
    await history(env, job.id, "published", "published");
  } else if (d.status === "SEND_TO_USER_INBOX") {
    await run(env.DB, "UPDATE publishing_jobs SET status = 'sent_to_inbox', updated_at = ? WHERE id = ?", now(), job.id);
    await history(env, job.id, "sent_to_inbox", "sent_to_inbox", { note: "Open the TikTok inbox notification to finish the post." });
  } else if (d.status === "FAILED") {
    await run(env.DB, "UPDATE publishing_jobs SET status = 'failed', fail_reason = ?, updated_at = ? WHERE id = ?",
      d.fail_reason ?? "failed", now(), job.id);
    await history(env, job.id, "failed", "failed", { fail_reason: d.fail_reason });
  }
}

export type { JobRow };
