/// <reference types="@cloudflare/workers-types" />
/**
 * Dashboard API (runs inside the Worker). Every request: Cloudflare Access JWT + email allowlist, then the per-visit
 * password (unlock token), then CSRF for mutations. All mutations are audit-logged.
 */
import { authenticate } from "../access";
import { audit, event } from "./audit";
import { normalizeTags, similarity, templateCaption } from "./captions";
import { runScheduled } from "./cron";
import { all, j, now, one, run, s, uuid } from "./db";
import { appUrl, configProblems, type Env, targetLogin } from "./env";
import { ensureSchema } from "./schema";
import { recommendations } from "./scheduler";
import { DEFAULTS, getSection, type Section, updateSection } from "./settings";
import * as tiktok from "./tiktok";
import * as twitch from "./twitch";
import * as unlock from "./unlock";

export class HttpError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

interface Ctx { env: Env; req: Request; url: URL; email: string; ip: string | null; params: string[] }

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

const bool = (v: unknown) => Boolean(v);
const MAX_UPLOAD = 95 * 1024 * 1024;
// Reachable without the per-visit password: the unlock flow itself, and media (unguessable keys only ever returned
// by unlocked endpoints; <video> elements cannot send headers).
const UNLOCK_EXEMPT = [/^session(\/|$)/, /^media\//];

export async function handleApi(req: Request, env: Env, path: string): Promise<Response> {
  try {
    await ensureSchema(env.DB);
    const auth = await authenticate(req.headers, { team: env.CF_ACCESS_TEAM_DOMAIN, aud: env.CF_ACCESS_AUD,
      emails: env.AUTHORIZED_EMAILS, devEmail: env.DEV_AUTH_EMAIL, production: env.CLIPX_ENV !== "development" });
    if (!auth.ok) return json({ detail: auth.reason }, auth.status);
    const url = new URL(req.url);
    const ctx: Ctx = { env, req, url, email: auth.email, ip: req.headers.get("cf-connecting-ip"), params: [] };
    if (!["GET", "HEAD"].includes(req.method)) {
      const origin = req.headers.get("origin");
      if (!origin || new URL(origin).host !== url.host) return json({ detail: "cross-origin request blocked" }, 403);
      const cookie = /(?:^|;\s*)clipx_csrf=([^;]+)/.exec(req.headers.get("cookie") ?? "")?.[1];
      if (!cookie || req.headers.get("x-clipx-csrf") !== decodeURIComponent(cookie)) return json({ detail: "missing or invalid CSRF token" }, 403);
    }
    if (configProblems(env).length && !path.startsWith("session")) return json({ detail: `Server not configured: ${configProblems(env).join("; ")}` }, 503);
    if (!UNLOCK_EXEMPT.some((r) => r.test(path)) && !(await unlock.verify(env, req.headers.get("x-clipx-unlock"), auth.email))) {
      return json({ detail: "locked" }, 401);
    }
    for (const [method, pattern, fn] of ROUTES) {
      if (method !== req.method) continue;
      const m = pattern.exec(path);
      if (m) return await fn({ ...ctx, params: m.slice(1) });
    }
    return json({ detail: "not found" }, 404);
  } catch (e) {
    if (e instanceof HttpError) return json({ detail: e.detail }, e.status);
    console.error("api error", e);
    return json({ detail: "internal error" }, 500);
  }
}

async function body<T = Record<string, unknown>>(c: Ctx): Promise<T> {
  const len = Number(c.req.headers.get("content-length") ?? 0);
  if (len > 1_000_000) throw new HttpError(413, "request too large");
  try {
    return (await c.req.json()) as T;
  } catch {
    throw new HttpError(422, "invalid JSON body");
  }
}

// ======================================================================================= serializers
const media = (key: string | null) => (key ? `/api/v1/media/${key}` : null);

function clipOut(r: Record<string, unknown>, detail = false) {
  const o: Record<string, unknown> = {
    id: r.id, origin: r.origin, stream_id: r.stream_id, title: r.title, clipper: r.clipper, view_count: r.view_count,
    score: r.score, selected: bool(r.selected), status: r.status, status_detail: r.status_detail, duration_s: r.duration,
    twitch_url: r.twitch_url, category: r.category, caption: r.caption, hashtags: j(r.hashtags, []),
    caption_source: r.caption_source, created_at: r.twitch_created_at ?? r.created_at, updated_at: r.updated_at,
    video_url: r.render_key ? `${media(r.render_key as string)}?v=${r.render_version}` : null,
    thumbnail_url: r.thumb_key ? `${media(r.thumb_key as string)}?v=${r.render_version}` : null,
    source_url: media(r.media_key as string | null), render_version: r.render_version,
  };
  if (detail) Object.assign(o, { words: j(r.words, []), cues: j(r.cues, []), cues_edited: bool(r.cues_edited), edit: j(r.edit, {}),
    transcript: r.transcript, rights_basis: r.rights_basis, vod_offset: r.vod_offset });
  return o;
}

// ======================================================================================= handlers
type Handler = (c: Ctx) => Promise<Response>;
const ROUTES: [string, RegExp, Handler][] = [];
const route = (method: string, pattern: string, fn: Handler) =>
  ROUTES.push([method, new RegExp(`^${pattern.replace(/:id/g, "([A-Za-z0-9_-]{1,80})")}$`), fn]);

// ---- session (per-visit password)
route("GET", "session", async (c) => json({ email: c.email, password_required: true,
  setup_required: !(await unlock.passwordHash(c.env)), min_length: unlock.MIN_PASSWORD,
  unlocked: await unlock.verify(c.env, c.req.headers.get("x-clipx-unlock"), c.email),
  attempts_left: await unlock.attemptsLeft(c.env, c.email), config_problems: configProblems(c.env) }));

route("POST", "session/setup", async (c) => {
  const { password } = await body<{ password?: string }>(c);
  if (typeof password !== "string" || password.length > 256) throw new HttpError(422, "password required");
  const r = await unlock.setup(c.env, c.email, password);
  if (r.ok) await audit(c.env.DB, c.email, "session.password_created", null, {}, c.ip);
  return r.ok ? json({ token: r.token, expires_at: r.expires_at }) : json({ detail: r.detail }, r.status);
});

route("POST", "settings/password", async (c) => {
  const { current, password } = await body<{ current?: string; password?: string }>(c);
  if (typeof current !== "string" || typeof password !== "string" || password.length > 256) throw new HttpError(422, "current and new password required");
  const r = await unlock.change(c.env, c.email, current, password);
  await audit(c.env.DB, c.email, r.ok ? "session.password_changed" : "session.password_change_failed", null, {}, c.ip);
  return r.ok ? json({ token: r.token, expires_at: r.expires_at }) : json({ detail: r.detail }, r.status);
});

route("POST", "session/unlock", async (c) => {
  const { password } = await body<{ password?: string }>(c);
  if (!password || password.length > 256) throw new HttpError(422, "password required");
  const r = await unlock.tryUnlock(c.env, c.email, password);
  await audit(c.env.DB, c.email, r.ok ? "session.unlocked" : "session.unlock_failed", null, {}, c.ip);
  return r.ok ? json({ token: r.token, expires_at: r.expires_at }) : json({ detail: r.detail }, r.status);
});

route("POST", "session/lock", async (c) => {
  await unlock.revoke(c.env, c.req.headers.get("x-clipx-unlock"));
  await audit(c.env.DB, c.email, "session.locked", null, {}, c.ip);
  return json({ status: "locked" });
});

// ---- media (streamed from R2, Range supported)
route("GET", "media/(clips/[A-Za-z0-9-]+/[a-z0-9_]+\\.(?:mp4|jpg))", async (c) => {
  const key = c.params[0];
  const range = c.req.headers.get("range");
  const head = await c.env.MEDIA.head(key);
  if (!head) throw new HttpError(404, "not found");
  const type = key.endsWith(".mp4") ? "video/mp4" : "image/jpeg";
  const m = range && /bytes=(\d+)-(\d*)/.exec(range);
  if (m) {
    const start = Number(m[1]);
    const end = m[2] ? Math.min(Number(m[2]), head.size - 1) : head.size - 1;
    const obj = await c.env.MEDIA.get(key, { range: { offset: start, length: end - start + 1 } });
    return new Response(obj?.body ?? null, { status: 206, headers: { "content-type": type, "accept-ranges": "bytes",
      "content-range": `bytes ${start}-${end}/${head.size}`, "content-length": String(end - start + 1), "cache-control": "private, max-age=3600" } });
  }
  const obj = await c.env.MEDIA.get(key);
  return new Response(obj?.body ?? null, { headers: { "content-type": type, "accept-ranges": "bytes",
    "content-length": String(head.size), "cache-control": "private, max-age=3600" } });
});

// ---- overview / live
route("GET", "overview", async (c) => {
  const db = c.env.DB;
  const live = await one(db, "SELECT * FROM streams WHERE status = 'live' ORDER BY started_at DESC LIMIT 1");
  const last = await one(db, "SELECT * FROM streams ORDER BY started_at DESC LIMIT 1");
  const cnt = async (sql: string, ...a: unknown[]) => ((await one<{ n: number }>(db, sql, ...a))?.n ?? 0);
  const since = now() - 14 * 86400;
  return json({
    live: live && streamOut(live), last_session: last && streamOut(last),
    counts: {
      clips: await cnt("SELECT COUNT(*) n FROM clips WHERE selected = 1 OR origin = 'viral_edit'"),
      rendered: await cnt("SELECT COUNT(*) n FROM clips WHERE status = 'rendered'"),
      to_process: await cnt("SELECT COUNT(*) n FROM clips WHERE status = 'media_ready'"),
      published: await cnt("SELECT COUNT(*) n FROM publishing_jobs WHERE status IN ('published','sent_to_inbox')"),
      awaiting_confirmation: await cnt("SELECT COUNT(*) n FROM publishing_jobs WHERE status = 'awaiting_confirmation'"),
    },
    clips_per_day: await all(db, `SELECT date(updated_at, 'unixepoch') AS date, COUNT(*) AS clips FROM clips
      WHERE status = 'rendered' AND updated_at >= ? GROUP BY 1 ORDER BY 1`, since),
    published_per_day: await all(db, `SELECT date(published_at, 'unixepoch') AS date, COUNT(*) AS published FROM publishing_jobs
      WHERE published_at >= ? GROUP BY 1 ORDER BY 1`, since),
    activity: (await all(db, "SELECT * FROM events ORDER BY at DESC LIMIT 12")).map(eventOut),
    capabilities: await twitch.capabilities(c.env),
  });
});

function streamOut(r: Record<string, unknown>) {
  return { id: r.id, title: r.title, game: r.game, status: r.status, started_at: r.started_at, ended_at: r.ended_at,
           detected_via: r.detected_via, clips_synced_at: r.clips_synced_at, stats: j(r.stats, {}) };
}
const eventOut = (e: Record<string, unknown>) => ({ at: e.at, level: e.level, component: e.component, event: e.event, message: e.message });

route("GET", "live/status", async (c) => {
  const live = await one(c.env.DB, "SELECT * FROM streams WHERE status = 'live' ORDER BY started_at DESC LIMIT 1");
  const cron = await one<{ value: string }>(c.env.DB, "SELECT value FROM cache WHERE key = 'cron:last'");
  const last = j<{ at?: number; report?: Record<string, unknown> }>(cron?.value, {});
  return json({ live: live && streamOut(live), target: targetLogin(c.env), cron: { last_run: last.at ?? null,
    alive: Boolean(last.at && now() - last.at < 180), report: last.report ?? null }, capabilities: await twitch.capabilities(c.env) });
});

route("GET", "live/timeline", async (c) => {
  const sid = c.url.searchParams.get("stream");
  const st = sid ? await one(c.env.DB, "SELECT * FROM streams WHERE id = ?", sid)
    : await one(c.env.DB, "SELECT * FROM streams ORDER BY started_at DESC LIMIT 1");
  if (!st) return json({ stream: null, clips: [], events: [], streams: [] });
  const clips = await all(c.env.DB, "SELECT * FROM clips WHERE stream_id = ? ORDER BY view_count DESC LIMIT 200", st.id);
  return json({ stream: streamOut(st), clips: clips.map((x) => ({ ...clipOut(x),
      offset_s: (x.vod_offset as number | null) ?? ((x.twitch_created_at as number) - (st.started_at as number) - ((x.duration as number) ?? 30)) })),
    events: (await all(c.env.DB, "SELECT * FROM events WHERE stream_id = ? ORDER BY at DESC LIMIT 100", st.id)).map(eventOut),
    streams: (await all(c.env.DB, "SELECT * FROM streams ORDER BY started_at DESC LIMIT 30")).map(streamOut) });
});

route("POST", "system/run-cron", async (c) => {
  const report = await runScheduled(c.env);
  await audit(c.env.DB, c.email, "system.run_cron", null, {}, c.ip);
  return json(report);
});

// ---- clips
route("GET", "clips", async (c) => {
  const p = c.url.searchParams;
  const where: string[] = ["(selected = 1 OR origin = 'viral_edit' OR status IN ('rendered','media_ready','processing'))"];
  const args: unknown[] = [];
  const status = p.get("status");
  if (status) { where.push("status = ?"); args.push(status); }
  const origin = p.get("origin");
  if (origin) { where.push("origin = ?"); args.push(origin); }
  const q = p.get("q");
  if (q) { where.push("(title LIKE ? OR caption LIKE ?)"); args.push(`%${q.slice(0, 100)}%`, `%${q.slice(0, 100)}%`); }
  const limit = Math.min(100, Number(p.get("limit") ?? 30) || 30);
  const offset = Math.max(0, Number(p.get("offset") ?? 0) || 0);
  const order = p.get("sort") === "views" ? "view_count DESC" : "updated_at DESC";
  const total = (await one<{ n: number }>(c.env.DB, `SELECT COUNT(*) n FROM clips WHERE ${where.join(" AND ")}`, ...args))?.n ?? 0;
  const rows = await all(c.env.DB, `SELECT * FROM clips WHERE ${where.join(" AND ")} ORDER BY ${order} LIMIT ? OFFSET ?`, ...args, limit, offset);
  return json({ total, items: rows.map((r) => clipOut(r)) });
});

async function getClip(c: Ctx) {
  const r = await one(c.env.DB, "SELECT * FROM clips WHERE id = ?", c.params[0]);
  if (!r) throw new HttpError(404, "clip not found");
  return r;
}

route("GET", "clips/:id", async (c) => {
  const r = await getClip(c);
  const jobs = await all(c.env.DB, "SELECT id, status, mode, scheduled_for, fail_reason FROM publishing_jobs WHERE clip_id = ? ORDER BY created_at DESC", r.id);
  return json({ ...clipOut(r, true), publishing: jobs });
});

route("PATCH", "clips/:id", async (c) => {
  const r = await getClip(c);
  const b = await body<{ title?: string; caption?: string; hashtags?: string[] }>(c);
  if (b.title !== undefined && (typeof b.title !== "string" || !b.title.trim() || b.title.length > 200)) throw new HttpError(422, "invalid title");
  if (b.caption !== undefined && (typeof b.caption !== "string" || b.caption.length > 2000)) throw new HttpError(422, "invalid caption");
  await run(c.env.DB, "UPDATE clips SET title = ?, caption = ?, hashtags = ?, caption_source = ?, updated_at = ? WHERE id = ?",
    b.title?.trim() ?? r.title, b.caption?.trim() ?? r.caption, b.hashtags ? s(normalizeTags(b.hashtags.slice(0, 8))) : r.hashtags,
    b.caption !== undefined ? "edited" : r.caption_source, now(), r.id);
  await audit(c.env.DB, c.email, "clip.update", r.id as string, b, c.ip);
  return json(clipOut((await getClip(c))));
});

const COLOR = /^#[0-9A-Fa-f]{6}$/;
route("PUT", "clips/:id/edit", async (c) => {
  const r = await getClip(c);
  const b = await body<Record<string, unknown>>(c);
  const e = j<Record<string, unknown>>(r.edit, {});
  const set = (k: string, ok: boolean, msg: string) => {
    if (b[k] === undefined) return;
    if (!ok) throw new HttpError(422, msg);
    e[k] = b[k];
  };
  set("layout", ["fit", "crop"].includes(String(b.layout)), "layout must be fit or crop");
  set("crop_x", b.crop_x === null || (typeof b.crop_x === "number" && b.crop_x >= 0 && b.crop_x <= 1), "crop_x must be 0..1");
  set("zoom_intensity", typeof b.zoom_intensity === "number" && b.zoom_intensity >= 0 && b.zoom_intensity <= 1.5, "zoom_intensity must be 0..1.5");
  set("trim_start", typeof b.trim_start === "number" && b.trim_start >= 0, "trim_start must be >= 0");
  set("trim_end", b.trim_end === null || (typeof b.trim_end === "number" && b.trim_end > 0), "trim_end must be > 0");
  set("sub_size", typeof b.sub_size === "number" && b.sub_size >= 50 && b.sub_size <= 140, "sub_size must be 50..140");
  set("sub_color", typeof b.sub_color === "string" && COLOR.test(b.sub_color), "sub_color must be #RRGGBB");
  set("sub_highlight", typeof b.sub_highlight === "string" && COLOR.test(b.sub_highlight), "sub_highlight must be #RRGGBB");
  set("sub_uppercase", typeof b.sub_uppercase === "boolean", "sub_uppercase must be boolean");
  if (typeof e.trim_end === "number" && typeof e.trim_start === "number" && e.trim_end <= e.trim_start + 3)
    throw new HttpError(422, "trim end must be at least 3 s after trim start");
  await run(c.env.DB, "UPDATE clips SET edit = ?, updated_at = ? WHERE id = ?", s(e), now(), r.id);
  await audit(c.env.DB, c.email, "clip.edit", r.id as string, b, c.ip);
  return json({ status: "saved", edit: e });
});

route("PUT", "clips/:id/cues", async (c) => {
  const r = await getClip(c);
  const { cues } = await body<{ cues?: { start: number; end: number; text: string; zone?: string; emphasis?: number[] }[] }>(c);
  if (!Array.isArray(cues) || cues.length > 400) throw new HttpError(422, "cues must be a list (max 400)");
  let prev = 0;
  const out = cues.slice().sort((a, b) => a.start - b.start).map((cue) => {
    if (typeof cue.text !== "string" || !cue.text.trim() || cue.text.length > 80) throw new HttpError(422, "each cue needs 1-80 characters of text");
    if (!(cue.end > cue.start) || cue.start < 0) throw new HttpError(422, `cue '${cue.text}' ends before it starts`);
    if (cue.start < prev - 0.01) throw new HttpError(422, `cue '${cue.text}' overlaps the previous cue`);
    prev = cue.end;
    const words = cue.text.trim().split(/\s+/);
    const step = (cue.end - cue.start) / words.length;
    return { start: cue.start, end: cue.end, zone: ["lower", "middle", "upper"].includes(cue.zone ?? "") ? cue.zone : "lower",
             words: words.map((w, i) => ({ w, start: cue.start + i * step, end: cue.start + (i + 1) * step, emph: (cue.emphasis ?? []).includes(i) })) };
  });
  await run(c.env.DB, "UPDATE clips SET cues = ?, cues_edited = 1, updated_at = ? WHERE id = ?", s(out), now(), r.id);
  await audit(c.env.DB, c.email, "clip.cues", r.id as string, { cues: out.length }, c.ip);
  return json({ status: "saved" });
});

route("POST", "clips/:id/select", async (c) => {
  const r = await getClip(c);
  if (!["discovered", "duplicate", "rejected"].includes(String(r.status))) throw new HttpError(409, `clip is ${r.status}`);
  await run(c.env.DB, "UPDATE clips SET selected = 1, status = ?, updated_at = ? WHERE id = ?", r.media_key ? "media_ready" : "needs_media", now(), r.id);
  await audit(c.env.DB, c.email, "clip.select", r.id as string, {}, c.ip);
  return json({ status: "selected" });
});

route("POST", "clips/:id/reject", async (c) => {
  const r = await getClip(c);
  await run(c.env.DB, "UPDATE clips SET status = 'rejected', selected = 0, status_detail = ?, updated_at = ? WHERE id = ?", `rejected by ${c.email}`, now(), r.id);
  await audit(c.env.DB, c.email, "clip.reject", r.id as string, {}, c.ip);
  return json({ status: "rejected" });
});

route("POST", "clips/:id/rerender", async (c) => {
  const r = await getClip(c);
  if (!r.media_key) throw new HttpError(409, "this clip has no source media");
  await run(c.env.DB, "UPDATE clips SET status = 'media_ready', status_detail = 'queued for re-render', claimed_until = NULL, updated_at = ? WHERE id = ?", now(), r.id);
  await audit(c.env.DB, c.email, "clip.rerender", r.id as string, {}, c.ip);
  return json({ status: "queued" });
});

// ---- browser processing protocol
route("GET", "queue", async (c) => json((await all(c.env.DB,
  "SELECT * FROM clips WHERE status = 'media_ready' ORDER BY score DESC, updated_at LIMIT 20")).map((r) => clipOut(r, true))));

route("POST", "clips/:id/claim", async (c) => {
  const n = await run(c.env.DB, `UPDATE clips SET status = 'processing', claimed_until = ?, status_detail = ?, updated_at = ?
    WHERE id = ? AND status = 'media_ready'`, now() + 900, `editing in ${c.email}'s browser`, now(), c.params[0]);
  if (!n) throw new HttpError(409, "clip is not waiting for processing (another tab may have it)");
  return json({ status: "claimed" });
});

route("POST", "clips/:id/release", async (c) => {
  const { error } = await body<{ error?: string }>(c);
  await run(c.env.DB, "UPDATE clips SET status = 'failed', status_detail = ?, claimed_until = NULL, updated_at = ? WHERE id = ? AND status = 'processing'",
    `browser render failed: ${String(error ?? "unknown").slice(0, 400)}`, now(), c.params[0]);
  return json({ status: "released" });
});

route("POST", "clips/:id/transcribe", async (c) => {
  const r = await getClip(c);
  if (!c.env.AI) throw new HttpError(503, "Workers AI binding is not configured");
  const len = Number(c.req.headers.get("content-length") ?? 0);
  if (!len || len > 8 * 1024 * 1024) throw new HttpError(413, "audio must be 1 byte - 8 MB (16 kHz mono WAV)");
  const audio = await c.req.arrayBuffer();
  // whisper-large-v3-turbo accepts the audio as a stream ({ body, contentType }) or base64; streaming avoids spending
  // the free plan's 10 ms CPU budget on base64 encoding. Output: segments[].words[] {word, start, end}.
  type Out = { text?: string; segments?: { words?: { word: string; start: number; end: number }[] }[] };
  const model = "@cf/openai/whisper-large-v3-turbo" as never;
  let out: Out;
  try {
    out = (await c.env.AI.run(model, { audio: { body: new Response(audio).body, contentType: "audio/wav" }, language: "en", vad_filter: true } as never)) as Out;
  } catch {
    const { b64 } = await import("./crypto");
    out = (await c.env.AI.run(model, { audio: b64(new Uint8Array(audio)), language: "en", vad_filter: true } as never)) as Out;
  }
  const words = (out.segments ?? []).flatMap((sg) => sg.words ?? []).map((w) => ({ w: w.word.trim(), start: w.start, end: w.end }))
    .filter((w) => w.w);
  await run(c.env.DB, "UPDATE clips SET words = ?, transcript = ?, updated_at = ? WHERE id = ?", s(words), (out.text ?? "").trim(), now(), r.id);
  return json({ words, text: out.text ?? "" });
});

async function putObject(c: Ctx, key: string, type: string) {
  const len = Number(c.req.headers.get("content-length") ?? 0);
  if (!len || len > MAX_UPLOAD || !c.req.body) throw new HttpError(413, "upload must be 1 byte - 95 MB");
  const { readable, writable } = new FixedLengthStream(len);
  c.req.body.pipeTo(writable);
  await c.env.MEDIA.put(key, readable, { httpMetadata: { contentType: type } });
  return len;
}

route("PUT", "clips/:id/thumb", async (c) => {
  const r = await getClip(c);
  const key = `clips/${r.id}/thumb.jpg`;
  await putObject(c, key, "image/jpeg");
  await run(c.env.DB, "UPDATE clips SET thumb_key = ?, updated_at = ? WHERE id = ?", key, now(), r.id);
  return json({ status: "saved" });
});

route("PUT", "clips/:id/render", async (c) => {
  const r = await getClip(c);
  if (r.status !== "processing") throw new HttpError(409, "claim the clip before uploading a render");
  const key = `clips/${r.id}/render.mp4`;
  const bytes = await putObject(c, key, "video/mp4");
  const dur = Number(c.url.searchParams.get("duration") ?? r.duration ?? 0);
  // caption (unique per clip), then the clip is ready
  let caption = r.caption as string | null, tags = r.hashtags as string, src = r.caption_source as string | null;
  if (!caption) {
    const recent = (await all<{ caption: string }>(c.env.DB, "SELECT caption FROM clips WHERE caption IS NOT NULL ORDER BY updated_at DESC LIMIT 40")).map((x) => x.caption);
    const st = await one<{ game: string | null }>(c.env.DB, "SELECT game FROM streams WHERE id = ?", r.stream_id);
    const cap = templateCaption({ clipId: r.id as string, title: r.title as string, category: r.category as string,
      transcript: (r.transcript as string) ?? "", game: st?.game, recent });
    caption = cap.caption; tags = s(cap.hashtags); src = cap.source;
  }
  await run(c.env.DB, `UPDATE clips SET render_key = ?, render_version = render_version + 1, status = 'rendered', status_detail = NULL,
    claimed_until = NULL, duration = ?, caption = ?, hashtags = ?, caption_source = ?, updated_at = ? WHERE id = ?`,
    key, dur || r.duration, caption, tags, src, now(), r.id);
  await event(c.env.DB, "INFO", "render", "rendered", `Rendered "${r.title}" in the browser (${(bytes / 1e6).toFixed(1)} MB)`, r.stream_id as string | null);
  await autoPrepare(c.env, r.id as string);
  return json({ status: "rendered", bytes });
});

async function nextSlot(env: Env, accountId: string): Promise<[number, string]> {
  const cfg = await getSection(env.DB, "publishing");
  const cap = await tiktok.capacity(env, cfg.max_posts_per_day, cfg.cooldown_minutes, accountId);
  const last = (await one<{ t: number | null }>(env.DB, `SELECT MAX(scheduled_for) t FROM publishing_jobs WHERE account_id = ?
    AND status IN ('scheduled','awaiting_confirmation')`, accountId))?.t;
  let earliest = cap.next_allowed_at;
  if (last && last + cfg.cooldown_minutes * 60 > earliest) earliest = last + cfg.cooldown_minutes * 60;
  const rec = await recommendations(env.DB, cfg.timezone);
  if (rec.status !== "ok") return [earliest, `Earliest time allowed by the cooldown and daily cap (no data-driven recommendation: ${rec.note})`];
  for (let day = 0; day < 3; day++) for (const w of rec.recommended) {
    const d = new Date((earliest + day * 86400) * 1000);
    d.setUTCHours(w.start_hour, 7, 0, 0);
    const t = Math.floor(d.getTime() / 1000);
    if (t >= earliest) return [t, `Recommended block ${w.start_hour}:00-${w.end_hour}:00 (n=${w.posts}, median views ${w.median_views})`];
  }
  return [earliest, "Earliest allowed time"];
}

async function autoPrepare(env: Env, clipId: string) {
  const cfg = await getSection(env.DB, "publishing");
  const acct = await tiktok.account(env);
  if (!cfg.auto_publish || !cfg.automation_consent_at || !acct) return;
  const [slot, basis] = await nextSlot(env, acct.open_id);
  await tiktok.prepare(env, clipId, cfg.auto_mode, slot, basis);
}

// ---- publishing
route("GET", "publishing/overview", async (c) => {
  const cfg = await getSection(c.env.DB, "publishing");
  const acct = await tiktok.account(c.env);
  const tok = acct ? await one<{ scopes: string; revoked: number; expires_at: number | null }>(c.env.DB,
    "SELECT scopes, revoked, expires_at FROM tokens WHERE id = ?", `tiktok:${acct.open_id}`) : null;
  return json({ account: acct && { ...acct, creator_info: j(acct.creator_info, null), scopes: j(tok?.scopes, []), revoked: bool(tok?.revoked) },
    settings: cfg, capacity: acct ? await tiktok.capacity(c.env, cfg.max_posts_per_day, cfg.cooldown_minutes, acct.open_id) : null,
    compliance: { consent_text: tiktok.CONSENT_TEXT, consent_text_branded: tiktok.CONSENT_TEXT_BRANDED,
      inbox_automation_consent: tiktok.INBOX_AUTOMATION_CONSENT,
      unaudited_restriction: cfg.client_audited ? null : "TikTok app not audited: Direct Post is limited to SELF_ONLY (private) and private accounts; max 5 posting users per 24 h." } });
});

async function jobsOut(env: Env, where: string, ...args: unknown[]): Promise<Record<string, unknown>[]> {
  const rows = await all(env.DB, `SELECT j.*, c.title AS clip_title, c.thumb_key, c.render_key, c.render_version, c.duration AS clip_duration
    FROM publishing_jobs j JOIN clips c ON c.id = j.clip_id WHERE ${where}`, ...args);
  return rows.map((r) => ({ ...r, post_ids: j(r.post_ids, []), thumbnail_url: r.thumb_key ? `${media(r.thumb_key as string)}?v=${r.render_version}` : null,
    video_url: r.render_key ? `${media(r.render_key as string)}?v=${r.render_version}` : null }));
}

route("GET", "publishing/queue", async (c) => json(await jobsOut(c.env,
  "j.status IN ('awaiting_confirmation','scheduled','uploading','processing') ORDER BY j.scheduled_for")));

route("GET", "publishing/history", async (c) => json({
  jobs: await jobsOut(c.env, "j.status IN ('published','sent_to_inbox','failed','cancelled') ORDER BY j.updated_at DESC LIMIT 100"),
  events: (await all(c.env.DB, "SELECT * FROM publishing_history ORDER BY at DESC LIMIT 200")).map((e) => ({ ...e, detail: j(e.detail, {}) })) }));

route("POST", "publishing/clips/:id/queue", async (c) => {
  const r = await getClip(c);
  const { mode } = await body<{ mode?: string }>(c);
  if (mode !== "direct_post" && mode !== "inbox_draft") throw new HttpError(422, "mode must be direct_post or inbox_draft");
  const acct = await tiktok.account(c.env);
  if (!acct) throw new HttpError(409, "connect a TikTok account first");
  if (r.status !== "rendered") throw new HttpError(409, "clip is not rendered yet");
  const others = await all<{ title: string; transcript: string | null }>(c.env.DB, `SELECT c.title, c.transcript FROM publishing_jobs j
    JOIN clips c ON c.id = j.clip_id WHERE j.account_id = ? AND j.status NOT IN ('failed','cancelled') AND j.clip_id != ?`, acct.open_id, r.id);
  const dup = others.find((o) => r.transcript && o.transcript && similarity(r.transcript as string, o.transcript) > 0.5);
  if (dup) throw new HttpError(409, `nearly identical to a clip already queued/published (${dup.title})`);
  const [slot, basis] = await nextSlot(c.env, acct.open_id);
  const id = await tiktok.prepare(c.env, r.id as string, mode, slot, basis, mode === "inbox_draft" ? c.email : null);
  await audit(c.env.DB, c.email, "publishing.queue", r.id as string, { mode }, c.ip);
  return json({ id });
});

route("GET", "publishing/jobs/:id/confirmation", async (c) => {
  const [job] = await jobsOut(c.env, "j.id = ?", c.params[0]);
  if (!job) throw new HttpError(404, "job not found");
  const acct = await tiktok.account(c.env);
  if (!acct) throw new HttpError(409, "no TikTok account connected");
  const info = await tiktok.refreshCreatorInfo(c.env, acct);
  const cfg = await getSection(c.env.DB, "publishing");
  const maxD = info.max_video_post_duration_sec as number | undefined;
  return json({ job, creator: { nickname: info.creator_nickname, username: info.creator_username, avatar_url: info.creator_avatar_url,
      privacy_level_options: info.privacy_level_options ?? [], comment_disabled: info.comment_disabled, duet_disabled: info.duet_disabled,
      stitch_disabled: info.stitch_disabled, max_video_post_duration_sec: maxD ?? null },
    duration_ok: !maxD || Number(job.clip_duration ?? 0) <= maxD, client_audited: cfg.client_audited,
    consent_text: tiktok.CONSENT_TEXT, consent_text_branded: tiktok.CONSENT_TEXT_BRANDED,
    processing_notice: "After publishing, it may take a few minutes for the video to process and appear on the TikTok profile." });
});

route("POST", "publishing/jobs/:id/confirm", async (c) => {
  const job = await one<Record<string, unknown>>(c.env.DB, "SELECT j.*, c.duration FROM publishing_jobs j JOIN clips c ON c.id = j.clip_id WHERE j.id = ?", c.params[0]);
  if (!job) throw new HttpError(404, "job not found");
  if (job.status !== "awaiting_confirmation") throw new HttpError(422, { errors: [`job is ${job.status}, not awaiting confirmation`] });
  const b = await body<Record<string, unknown>>(c);
  const acct = await tiktok.account(c.env);
  if (!acct?.creator_info || !acct.creator_info_at || now() - acct.creator_info_at > 600) throw new HttpError(422, { errors: ["creator info is stale: reload the confirmation card"] });
  const cfg = await getSection(c.env.DB, "publishing");
  const title = String(b.title ?? "");
  const errors = tiktok.validateChoices(j(acct.creator_info, {}), { privacy_level: (b.privacy_level as string) || null,
    allow_comment: bool(b.allow_comment), allow_duet: bool(b.allow_duet), allow_stitch: bool(b.allow_stitch), consent: b.consent === true,
    duration_s: Number(job.duration ?? 0), brand_content: bool(b.brand_content), brand_organic: bool(b.brand_organic),
    disclosure_on: bool(b.disclosure_on), client_audited: cfg.client_audited });
  if (title.length > 2200) errors.push("title exceeds 2200 characters");
  if (errors.length) throw new HttpError(422, { errors });
  await run(c.env.DB, `UPDATE publishing_jobs SET status = 'scheduled', privacy_level = ?, allow_comment = ?, allow_duet = ?, allow_stitch = ?,
    brand_content = ?, brand_organic = ?, title = ?, consent_at = ?, confirmed_by = ?, scheduled_for = ?, updated_at = ? WHERE id = ?`,
    b.privacy_level, Number(bool(b.allow_comment)), Number(bool(b.allow_duet)), Number(bool(b.allow_stitch)), Number(bool(b.brand_content)),
    Number(bool(b.brand_organic)), title, now(), c.email, now(), now(), job.id);
  await tiktok.history(c.env, job.id as string, "confirmed", "scheduled", { by: c.email, privacy: b.privacy_level,
    consent: tiktok.consentText(bool(b.brand_content)) });
  await audit(c.env.DB, c.email, "publishing.confirm", job.id as string, { privacy: b.privacy_level }, c.ip);
  return json({ status: "scheduled" });
});

route("POST", "publishing/jobs/:id/cancel", async (c) => {
  const n = await run(c.env.DB, "UPDATE publishing_jobs SET status = 'cancelled', updated_at = ? WHERE id = ? AND status IN ('awaiting_confirmation','scheduled')", now(), c.params[0]);
  if (!n) throw new HttpError(409, "only queued jobs can be cancelled");
  await tiktok.history(c.env, c.params[0], "cancelled", "cancelled", { by: c.email });
  await audit(c.env.DB, c.email, "publishing.cancel", c.params[0], {}, c.ip);
  return json({ status: "cancelled" });
});

// ---- analytics
route("GET", "analytics/summary", async (c) => {
  const rows = await all<Record<string, number | string | null>>(c.env.DB, `SELECT a.* FROM analytics a JOIN (SELECT video_id, MAX(captured_at) m
    FROM analytics GROUP BY video_id) l ON l.video_id = a.video_id AND l.m = a.captured_at`);
  const measured = rows.filter((r) => r.views !== null);
  const tot = { views: 0, likes: 0, comments: 0, shares: 0 } as Record<string, number>;
  for (const r of measured) for (const k of Object.keys(tot)) tot[k] += Number(r[k] ?? 0);
  const byDay = new Map<string, Record<string, number>>();
  for (const r of measured) {
    if (!r.posted_at) continue;
    const d = new Date(Number(r.posted_at) * 1000).toISOString().slice(0, 10);
    const x = byDay.get(d) ?? { views: 0, likes: 0, shares: 0, comments: 0, posts: 0 };
    for (const k of ["views", "likes", "shares", "comments"]) x[k] += Number(r[k] ?? 0);
    x.posts++;
    byDay.set(d, x);
  }
  const cfg = await getSection(c.env.DB, "publishing");
  return json({ videos: measured.length, totals: tot,
    engagement_rate: tot.views ? Math.round(((tot.likes + tot.comments + tot.shares) / tot.views) * 10000) / 10000 : null,
    retention: { available: false, note: "TikTok's API does not expose retention or watch-time data." },
    daily: [...byDay.entries()].sort().map(([date, v]) => ({ date, ...v })),
    best: measured.sort((a, b) => Number(b.views) - Number(a.views)).slice(0, 10),
    last_synced: rows.reduce((m, r) => Math.max(m, Number(r.captured_at)), 0) || null,
    posting_times: await recommendations(c.env.DB, cfg.timezone) });
});

route("POST", "analytics/sync", async (c) => {
  const { syncAnalytics } = await import("./cron");
  const r = await syncAnalytics(c.env);
  await audit(c.env.DB, c.email, "analytics.sync", null, r, c.ip);
  return json(r);
});

// ---- viral edits (sources = official Twitch clip metadata; edits are rendered in the browser from authorized clips)
route("GET", "viral/sources", async (c) => json(await all(c.env.DB, "SELECT * FROM viral_sources ORDER BY popularity DESC LIMIT 60")));

route("POST", "viral/discover", async (c) => {
  const { discoverViral } = await import("./cron");
  const r = await discoverViral(c.env);
  await audit(c.env.DB, c.email, "viral.discover", null, r, c.ip);
  return json(r);
});

const TEMPLATES = [
  { template: "pov", name: "POV", min: 1, max: 1, description: "One clip with a POV banner framing the moment from the viewer's side.", patterns: [/\bpov\b/i] },
  { template: "compilation", name: "Compilation", min: 3, max: 5, description: "3-5 of the most viewed moments, numbered.", patterns: [/compilation|best of|top \d+/i] },
  { template: "how_me_and_bro_look", name: "How me and bro look", min: 2, max: 2, description: "Two moments stacked with a shared caption.", patterns: [/me and (my )?bro/i] },
];

route("GET", "viral/templates", async (c) => {
  const sources = await all<{ title: string | null }>(c.env.DB, "SELECT title FROM viral_sources ORDER BY popularity DESC LIMIT 50");
  const ready = await all<{ id: string }>(c.env.DB, "SELECT id FROM clips WHERE status = 'rendered' AND origin = 'twitch_clip' ORDER BY view_count DESC LIMIT 20");
  return json(TEMPLATES.map((t) => {
    const hits = sources.filter((x) => x.title && t.patterns.some((p) => p.test(x.title!)));
    const evidence = hits.length ? [`${hits.length} of the top ${sources.length} popular Lacy clips use this format`]
      : ["no trend evidence yet — available as a manual template"];
    if (ready.length < t.min) evidence.push(`needs ${t.min} rendered clips (have ${ready.length})`);
    return { template: t.template, name: t.name, description: t.description, min: t.min, max: t.max, evidence,
             ready: ready.length >= t.min, suggested_clip_ids: ready.slice(0, t.max).map((x) => x.id) };
  }));
});

route("GET", "viral/edits", async (c) => json((await all(c.env.DB,
  "SELECT e.*, c.render_key, c.thumb_key, c.render_version, c.title AS out_title FROM viral_edits e LEFT JOIN clips c ON c.id = e.output_clip_id ORDER BY e.created_at DESC LIMIT 60"))
  .map((e) => ({ ...e, input_clip_ids: j(e.input_clip_ids, []), video_url: e.render_key ? `${media(e.render_key as string)}?v=${e.render_version}` : null,
    thumbnail_url: e.thumb_key ? `${media(e.thumb_key as string)}?v=${e.render_version}` : null }))));

route("POST", "viral/edits", async (c) => {
  const b = await body<{ template?: string; clip_ids?: string[]; text?: string }>(c);
  const t = TEMPLATES.find((x) => x.template === b.template);
  if (!t) throw new HttpError(422, "unknown template");
  const ids = (b.clip_ids ?? []).filter((x) => typeof x === "string").slice(0, 5);
  if (ids.length < t.min || ids.length > t.max) throw new HttpError(422, `${t.name} needs ${t.min}-${t.max} clips`);
  for (const id of ids) {
    const r = await one<{ status: string; rights_basis: string | null; title: string }>(c.env.DB, "SELECT status, rights_basis, title FROM clips WHERE id = ?", id);
    if (!r) throw new HttpError(404, "clip not found");
    if (r.status !== "rendered" || !r.rights_basis) throw new HttpError(403, `clip '${r.title}' has no rendered, authorized media`);
  }
  const id = uuid();
  await run(c.env.DB, "INSERT INTO viral_edits (id, template, text, input_clip_ids, status, created_by, created_at) VALUES (?, ?, ?, ?, 'queued', ?, ?)",
    id, t.template, String(b.text ?? "").slice(0, 120), s(ids), c.email, now());
  await audit(c.env.DB, c.email, "viral.create", id, { template: t.template }, c.ip);
  return json({ id });
});

route("PUT", "viral/edits/:id/render", async (c) => {
  const e = await one<{ id: string; template: string; text: string; input_clip_ids: string; output_clip_id: string | null }>(c.env.DB,
    "SELECT * FROM viral_edits WHERE id = ?", c.params[0]);
  if (!e) throw new HttpError(404, "edit not found");
  const clipId = e.output_clip_id ?? uuid();
  const key = `clips/${clipId}/render.mp4`;
  await putObject(c, key, "video/mp4");
  const inputs = j<string[]>(e.input_clip_ids, []);
  const name = TEMPLATES.find((t) => t.template === e.template)?.name ?? e.template;
  if (!e.output_clip_id) {
    await run(c.env.DB, `INSERT INTO clips (id, origin, title, status, render_key, render_version, rights_basis, category, duration, created_at, updated_at)
      VALUES (?, 'viral_edit', ?, 'rendered', ?, 1, ?, 'moment', ?, ?, ?)`, clipId, `${name}: ${e.text || "Lacy"}`.slice(0, 200), key,
      `Composed in the browser from authorized clips ${inputs.join(", ")}`, Number(c.url.searchParams.get("duration") ?? 0) || null, now(), now());
  } else {
    await run(c.env.DB, "UPDATE clips SET render_version = render_version + 1, updated_at = ? WHERE id = ?", now(), clipId);
  }
  const cap = templateCaption({ clipId, title: name, category: "moment", transcript: "", recent: [] });
  await run(c.env.DB, "UPDATE clips SET caption = COALESCE(caption, ?), hashtags = ?, caption_source = COALESCE(caption_source, 'template') WHERE id = ?",
    cap.caption, s(cap.hashtags), clipId);
  await run(c.env.DB, "UPDATE viral_edits SET output_clip_id = ?, status = 'rendered' WHERE id = ?", clipId, e.id);
  return json({ clip_id: clipId });
});

// ---- settings / security
route("GET", "settings", async (c) => {
  const env = c.env;
  return json({ processing: await getSection(env.DB, "processing"), publishing: await getSection(env.DB, "publishing"),
    system: { plan: "Cloudflare free (Workers + D1 + R2 + Workers AI + Cron)", twitch_app_configured: twitch.configured(env),
      twitch_target_login: targetLogin(env), tiktok_app_configured: tiktok.configured(env), workers_ai: Boolean(env.AI),
      password_required: true, password_in_dashboard: unlock.managedInDashboard(env), access_configured: Boolean(env.CF_ACCESS_TEAM_DOMAIN && env.CF_ACCESS_AUD),
      authorized_emails: (env.AUTHORIZED_EMAILS ?? "").split(",").map((x) => x.trim()).filter(Boolean),
      twitch_redirect_uri: `${appUrl(env, c.url.origin)}/api/oauth/twitch/callback`,
      tiktok_redirect_uri: `${appUrl(env, c.url.origin)}/api/oauth/tiktok/callback/`, config_problems: configProblems(env) } });
});

route("PUT", "settings/(processing|publishing)", async (c) => {
  const section = c.params[0] as Section;
  const patch = await body<Record<string, unknown>>(c);
  if (section === "publishing") {
    delete patch.automation_consent_by;
    delete patch.automation_consent_at;
    const consent = patch.automation_consent;
    delete patch.automation_consent;
    const cur = await getSection(c.env.DB, "publishing");
    const enabling = (patch.auto_publish as boolean | undefined) ?? cur.auto_publish;
    if (enabling && !cur.automation_consent_at && consent !== true)
      throw new HttpError(422, "Enabling automatic publishing requires explicit consent (automation_consent: true) from the account owner.");
    if (consent === true) { patch.automation_consent_by = c.email; patch.automation_consent_at = now(); }
    if (patch.auto_publish === false) { patch.automation_consent_by = null; patch.automation_consent_at = null; }
  }
  let v;
  try {
    v = await updateSection(c.env.DB, section, patch, c.email);
  } catch (e) {
    throw new HttpError(422, String(e instanceof Error ? e.message : e));
  }
  await audit(c.env.DB, c.email, `settings.${section}.update`, null, patch, c.ip);
  return json(v);
});

route("GET", "audit", async (c) => json((await all(c.env.DB, "SELECT * FROM audit_logs ORDER BY at DESC LIMIT 100")).map((a) => ({ ...a, detail: j(a.detail, {}) }))));
route("GET", "events", async (c) => json((await all(c.env.DB, "SELECT * FROM events ORDER BY at DESC LIMIT 100")).map(eventOut)));

// ---- integrations
route("GET", "integrations/twitch", async (c) => {
  let target: Record<string, unknown> | null = null;
  if (twitch.configured(c.env)) {
    try { target = await twitch.target(c.env) as unknown as Record<string, unknown>; } catch (e) { target = { error: String(e) }; }
  }
  return json({ configured: twitch.configured(c.env), target, accounts: await twitch.accounts(c.env), capabilities: await twitch.capabilities(c.env),
    redirect_uri: `${appUrl(c.env, c.url.origin)}/api/oauth/twitch/callback` });
});

route("POST", "integrations/twitch/connect", async (c) => {
  const { role } = await body<{ role?: string }>(c);
  try {
    const url = await twitch.startConnect(c.env, role ?? "operator", c.email, `${appUrl(c.env, c.url.origin)}/api/oauth/twitch/callback`);
    await audit(c.env.DB, c.email, "twitch.connect.start", role ?? "operator", {}, c.ip);
    return json({ authorize_url: url });
  } catch (e) {
    throw new HttpError(409, String(e instanceof Error ? e.message : e));
  }
});

route("DELETE", "integrations/twitch/accounts/(\\d{1,20})", async (c) => {
  await run(c.env.DB, "DELETE FROM twitch_accounts WHERE user_id = ?", c.params[0]);
  await run(c.env.DB, "DELETE FROM tokens WHERE id = ?", `twitch:${c.params[0]}`);
  await audit(c.env.DB, c.email, "twitch.disconnect", c.params[0], {}, c.ip);
  return json({ status: "disconnected" });
});

route("GET", "integrations/tiktok", async (c) => json({ configured: tiktok.configured(c.env), account: await tiktok.account(c.env),
  scopes_requested: tiktok.SCOPES.split(","), redirect_uri: `${appUrl(c.env, c.url.origin)}/api/oauth/tiktok/callback/` }));

route("POST", "integrations/tiktok/connect", async (c) => {
  try {
    const url = await tiktok.startConnect(c.env, c.email, `${appUrl(c.env, c.url.origin)}/api/oauth/tiktok/callback/`);
    await audit(c.env.DB, c.email, "tiktok.connect.start", null, {}, c.ip);
    return json({ authorize_url: url });
  } catch (e) {
    throw new HttpError(409, String(e instanceof Error ? e.message : e));
  }
});

route("DELETE", "integrations/tiktok/accounts/([A-Za-z0-9._-]{1,120})", async (c) => {
  await run(c.env.DB, "DELETE FROM tiktok_accounts WHERE open_id = ?", c.params[0]);
  await run(c.env.DB, "DELETE FROM tokens WHERE id = ?", `tiktok:${c.params[0]}`);
  await audit(c.env.DB, c.email, "tiktok.disconnect", c.params[0], {}, c.ip);
  return json({ status: "disconnected" });
});

export { DEFAULTS };
