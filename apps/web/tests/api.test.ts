import { beforeEach, describe, expect, it, vi } from "vitest";
import { handleApi } from "../lib/server/api";
import { hashPassword } from "../lib/server/crypto";
import type { Env } from "../lib/server/env";
import { resetSchemaFlag } from "../lib/server/schema";
import { d1, installWorkerGlobals, r2 } from "./helpers/cf";

installWorkerGlobals();
const ORIGIN = "https://clipx.test";
let env: Env & { DB: ReturnType<typeof d1>; MEDIA: ReturnType<typeof r2> };
let token = "";

async function call(method: string, path: string, opts: { body?: unknown; raw?: Uint8Array; headers?: Record<string, string>; unlock?: boolean; csrf?: boolean } = {}) {
  const h = new Headers(opts.headers);
  if (method !== "GET") {
    h.set("origin", ORIGIN);
    if (opts.csrf !== false) { h.set("cookie", "clipx_csrf=abc"); h.set("x-clipx-csrf", "abc"); }
  }
  if (opts.unlock !== false && token) h.set("x-clipx-unlock", token);
  let body: BodyInit | undefined;
  if (opts.body !== undefined) { body = JSON.stringify(opts.body); h.set("content-type", "application/json"); }
  if (opts.raw) { body = opts.raw as BodyInit; h.set("content-length", String(opts.raw.byteLength)); }
  const res = await handleApi(new Request(`${ORIGIN}/api/v1/${path}`, { method, headers: h, body }), env, path.split("?")[0]);
  const text = await res.text();
  let data: unknown = text;
  try { data = JSON.parse(text); } catch { /* binary */ }
  return { status: res.status, data: data as Record<string, any>, headers: res.headers };
}

beforeEach(async () => {
  vi.stubEnv("DEV_AUTH_EMAIL", "op@example.com");
  vi.stubEnv("CF_ACCESS_TEAM_DOMAIN", "");
  resetSchemaFlag();
  env = { DB: d1(), MEDIA: r2(), CLIPX_ENV: "development", DEV_AUTH_EMAIL: "op@example.com", SESSION_SECRET: "s".repeat(40), TOKEN_ENCRYPTION_KEY: Buffer.alloc(32, 7).toString("base64"),
          DASHBOARD_PASSWORD_HASH: await hashPassword("correct horse", 1000) } as never;
  token = "";
});

async function unlock() {
  const r = await call("POST", "session/unlock", { body: { password: "correct horse" } });
  expect(r.status).toBe(200);
  token = r.data.token;
}

describe("per-visit password", () => {
  it("locks everything except the session endpoints until the password is entered", async () => {
    expect((await call("GET", "overview")).status).toBe(401);
    const s = await call("GET", "session");
    expect(s.data).toMatchObject({ email: "op@example.com", password_required: true, unlocked: false });
    const bad = await call("POST", "session/unlock", { body: { password: "nope" } });
    expect(bad.status).toBe(401);
    expect(bad.data.detail).toMatch(/4 attempts left/);
    await unlock();
    expect((await call("GET", "overview")).status).toBe(200);
    await call("POST", "session/lock");
    expect((await call("GET", "overview")).status).toBe(401);
  });

  it("locks out after five wrong passwords", async () => {
    for (let i = 0; i < 5; i++) await call("POST", "session/unlock", { body: { password: "x" } });
    const r = await call("POST", "session/unlock", { body: { password: "correct horse" } });
    expect(r.status).toBe(429);
  });

  it("fails closed in production without Cloudflare Access", async () => {
    env.CLIPX_ENV = "production";
    expect((await call("GET", "overview")).status).toBe(503);
  });

  it("lets the first authorized visitor create the password exactly once, then requires it", async () => {
    env.DASHBOARD_PASSWORD_HASH = undefined;
    const s = await call("GET", "session");
    expect(s.data).toMatchObject({ setup_required: true, unlocked: false });
    expect((await call("GET", "overview")).status).toBe(401);
    expect((await call("POST", "session/setup", { body: { password: "short" } })).status).toBe(422);
    const ok = await call("POST", "session/setup", { body: { password: "a-long-enough-password" } });
    expect(ok.status).toBe(200);
    expect((await call("POST", "session/setup", { body: { password: "another-long-password" } })).status).toBe(409);
    token = ok.data.token;
    expect((await call("GET", "overview")).status).toBe(200);
    expect((await call("GET", "session")).data.setup_required).toBe(false);
    // change: wrong current password is refused; a change signs out other sessions
    expect((await call("POST", "settings/password", { body: { current: "nope", password: "a-brand-new-password" } })).status).toBe(401);
    const old = token;
    const ch = await call("POST", "settings/password", { body: { current: "a-long-enough-password", password: "a-brand-new-password" } });
    expect(ch.status).toBe(200);
    token = old;
    expect((await call("GET", "overview")).status).toBe(401);
    token = "";
    expect((await call("POST", "session/unlock", { body: { password: "a-brand-new-password" } })).status).toBe(200);
  });
});

describe("csrf", () => {
  it("rejects mutations without the double-submit token or from another origin", async () => {
    await unlock();
    expect((await call("POST", "system/run-cron", { csrf: false })).status).toBe(403);
    const r = await handleApi(new Request(`${ORIGIN}/api/v1/system/run-cron`, { method: "POST",
      headers: { origin: "https://evil.test", cookie: "clipx_csrf=abc", "x-clipx-csrf": "abc", "x-clipx-unlock": token } }), env, "system/run-cron");
    expect(r.status).toBe(403);
  });
});

function seedClip(id: string, status = "media_ready") {
  env.DB.raw.prepare(`INSERT INTO clips (id, stream_id, twitch_clip_id, title, view_count, duration, selected, status, media_key, rights_basis, created_at, updated_at)
    VALUES (?, 's1', ?, 'Big moment', 50, 30, 1, ?, ?, 'editor', 1, 1)`).run(id, `tw-${id}`, status, `clips/${id}/source.mp4`);
}

describe("browser processing protocol", () => {
  it("claims, uploads a render, stores a unique caption and serves media with Range", async () => {
    await unlock();
    await call("GET", "session"); // migrate
    seedClip("c1");
    expect((await call("GET", "queue")).data).toHaveLength(1);
    expect((await call("PUT", "clips/c1/render", { raw: new Uint8Array([1]) })).status).toBe(409); // not claimed
    expect((await call("POST", "clips/c1/claim")).status).toBe(200);
    expect((await call("POST", "clips/c1/claim")).status).toBe(409); // second tab
    const bytes = new Uint8Array(1000).map((_, i) => i % 256);
    const up = await call("PUT", "clips/c1/render?duration=29.5", { raw: bytes });
    expect(up.status).toBe(200);
    const clip = (await call("GET", "clips/c1")).data;
    expect(clip.status).toBe("rendered");
    expect(clip.duration_s).toBe(29.5);
    expect(clip.hashtags[0]).toBe("#Lacy");
    expect(clip.video_url).toBe("/api/v1/media/clips/c1/render.mp4?v=1");
    const part = await call("GET", "media/clips/c1/render.mp4", { headers: { range: "bytes=10-19" } });
    expect(part.status).toBe(206);
    expect(part.headers.get("content-range")).toBe("bytes 10-19/1000");
  });

  it("validates subtitle cues and edits", async () => {
    await unlock();
    await call("GET", "session");
    seedClip("c2", "rendered");
    expect((await call("PUT", "clips/c2/cues", { body: { cues: [{ start: 2, end: 1, text: "x" }] } })).status).toBe(422);
    expect((await call("PUT", "clips/c2/cues", { body: { cues: [{ start: 0, end: 1.2, text: "NO WAY", emphasis: [1] }] } })).status).toBe(200);
    expect((await call("PUT", "clips/c2/edit", { body: { sub_color: "red" } })).status).toBe(422);
    const ok = await call("PUT", "clips/c2/edit", { body: { layout: "crop", crop_x: 0.4, sub_color: "#FFE11A" } });
    expect(ok.data.edit).toMatchObject({ layout: "crop", crop_x: 0.4 });
  });
});

describe("publishing", () => {
  it("requires a connected TikTok account and explicit consent for automation", async () => {
    await unlock();
    await call("GET", "session");
    seedClip("c3", "rendered");
    expect((await call("POST", "publishing/clips/c3/queue", { body: { mode: "direct_post" } })).status).toBe(409);
    const noConsent = await call("PUT", "settings/publishing", { body: { auto_publish: true } });
    expect(noConsent.status).toBe(422);
    const yes = await call("PUT", "settings/publishing", { body: { auto_publish: true, automation_consent: true } });
    expect(yes.status).toBe(200);
    expect(yes.data.automation_consent_by).toBe("op@example.com");
  });
});

describe("analytics", () => {
  it("never invents numbers when nothing has been published", async () => {
    await unlock();
    const r = await call("GET", "analytics/summary");
    expect(r.data).toMatchObject({ videos: 0, engagement_rate: null, retention: { available: false } });
    expect(r.data.posting_times.status).toBe("insufficient_data");
  });
});
