/** Cron pipeline against mocked Twitch responses (labelled fixtures — not real data). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { detectLive, fetchMedia, rankAndSelect, syncClips } from "../lib/server/cron";
import type { Env } from "../lib/server/env";
import { ensureSchema, resetSchemaFlag } from "../lib/server/schema";
import { storeToken } from "../lib/server/twitch";
import { d1, installWorkerGlobals, r2 } from "./helpers/cf";

installWorkerGlobals();
let env: Env & { DB: ReturnType<typeof d1>; MEDIA: ReturnType<typeof r2> };
const now = () => Math.floor(Date.now() / 1000);
const iso = (t: number) => new Date(t * 1000).toISOString();
const STARTED = now() - 3 * 3600;

type Handler = (url: URL, init?: RequestInit) => Response | Promise<Response>;
let routes: [RegExp, Handler][] = [];
const calls: string[] = [];
const on = (re: RegExp, h: Handler) => routes.push([re, h]);
const ok = (data: unknown) => Response.json(data);

beforeEach(async () => {
  resetSchemaFlag();
  env = { DB: d1(), MEDIA: r2(), TWITCH_CLIENT_ID: "fixture-client", TWITCH_CLIENT_SECRET: "fixture-secret",
          TOKEN_ENCRYPTION_KEY: Buffer.alloc(32, 7).toString("base64"), TWITCH_TARGET_LOGIN: "lacy" } as never;
  await ensureSchema(env.DB);
  routes = [];
  calls.length = 0;
  on(/id\.twitch\.tv\/oauth2\/token/, () => ok({ access_token: "fixture-app", expires_in: 5000000, token_type: "bearer" }));
  on(/helix\/users\?login=lacy/, () => ok({ data: [{ id: "494543675", login: "lacy", display_name: "Lacy" }] }));
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input instanceof Request ? input.url : input));
    calls.push(`${init?.method ?? "GET"} ${url.host}${url.pathname}`);
    for (const [re, h] of routes) if (re.test(url.toString())) return h(url, init);
    return new Response("unmocked " + url, { status: 599 });
  });
});
afterEach(() => vi.unstubAllGlobals());

const clip = (id: string, views: number, offset: number | null, duration = 30) => ({ id, url: `https://clips.twitch.tv/${id}`,
  title: `fixture ${id}`, creator_name: "viewer", view_count: views, created_at: iso(STARTED + (offset ?? 0) + duration),
  duration, vod_offset: offset, video_id: "v1" });

describe("live detection", () => {
  it("records going live and offline exactly once", async () => {
    let live = true;
    on(/helix\/streams/, () => ok({ data: live ? [{ id: "st1", user_id: "494543675", title: "fixture stream", game_name: "Fortnite",
      started_at: iso(STARTED), type: "live" }] : [] }));
    expect(await detectLive(env)).toEqual({ state: "went_live", stream: "st1" });
    expect(await detectLive(env)).toEqual({ state: "live" });
    live = false;
    expect(await detectLive(env)).toEqual({ state: "went_offline", stream: "st1" });
    const st = await env.DB.prepare("SELECT status, ended_at FROM streams").first<{ status: string; ended_at: number }>();
    expect(st?.status).toBe("ended");
  });
});

describe("viewer clip ranking", () => {
  beforeEach(() => {
    env.DB.raw.prepare("INSERT INTO streams (id, broadcaster_id, started_at, status) VALUES ('st1', '494543675', ?, 'live')").run(STARTED);
  });

  it("selects the most viewed moments, drops overlapping duplicates and never pads", async () => {
    on(/helix\/clips\?/, () => ok({ data: [
      clip("a", 900, 100), clip("a-dup", 300, 105), clip("b", 400, 1200), clip("c", 1, 2000), clip("d", 50, null),
    ] }));
    const r = await syncClips(env);
    expect(r).toMatchObject({ viewer_clips: 5, selected: 3, duplicates: 1, below_threshold: 1 });
    const rows = env.DB.raw.prepare("SELECT twitch_clip_id t, status, selected, score FROM clips ORDER BY view_count DESC").all() as Record<string, unknown>[];
    expect(rows.map((x) => [x.t, x.status])).toEqual([["a", "needs_media"], ["b", "needs_media"], ["a-dup", "duplicate"],
      ["d", "needs_media"], ["c", "discovered"]]);
    expect(rows[0].score).toBe(1);
    // the request asked Twitch for clips inside the stream window
    expect(calls.filter((c) => c.includes("/helix/clips"))).toHaveLength(1);
    // not due again for 5 minutes
    expect(await syncClips(env)).toEqual({ synced: null });
  });

  it("respects the per-stream maximum", async () => {
    env.DB.raw.prepare("INSERT INTO settings (key, value) VALUES ('processing', ?)").run(JSON.stringify({ target_clips_max: 2 }));
    on(/helix\/clips\?/, () => ok({ data: [clip("a", 90, 0), clip("b", 80, 100), clip("c", 70, 200)] }));
    expect(await syncClips(env)).toMatchObject({ selected: 2 });
  });

  it("does not touch clips that are already being edited", async () => {
    env.DB.raw.prepare(`INSERT INTO clips (id, stream_id, twitch_clip_id, title, view_count, vod_offset, duration, selected, status, created_at, updated_at)
      VALUES ('x', 'st1', 'old', 't', 5, 100, 30, 1, 'rendered', 1, 1)`).run();
    on(/helix\/clips\?/, () => ok({ data: [clip("old", 5, 100), clip("new", 500, 102)] }));
    await syncClips(env);
    const rows = Object.fromEntries((env.DB.raw.prepare("SELECT twitch_clip_id t, status FROM clips").all() as { t: string; status: string }[]).map((x) => [x.t, x.status]));
    expect(rows).toEqual({ old: "rendered", new: "needs_media" });
    await rankAndSelect(env, { id: "st1", broadcaster_id: "1", started_at: STARTED, ended_at: null, status: "live", clips_synced_at: null, title: null, game: null });
  });
});

describe("authorized media", () => {
  beforeEach(() => {
    env.DB.raw.prepare("INSERT INTO streams (id, broadcaster_id, started_at, status) VALUES ('st1', '494543675', ?, 'live')").run(STARTED);
    env.DB.raw.prepare(`INSERT INTO clips (id, stream_id, twitch_clip_id, title, view_count, duration, selected, status, score, created_at, updated_at)
      VALUES ('c1', 'st1', 'tw1', 't', 10, 30, 1, 'needs_media', 1, 1, 1)`).run();
  });

  it("waits for an editor or broadcaster instead of downloading anything", async () => {
    expect(await fetchMedia(env, 3)).toMatchObject({ fetched: 0, blocked: "no editor/broadcaster authorization" });
    expect(calls).toHaveLength(0);
  });

  async function connectEditor() {
    await storeToken(env, "twitch", "777", { access_token: "fixture-user", refresh_token: "r", expires_in: 14000, scopes: ["clips:edit", "editor:manage:clips"] });
    env.DB.raw.prepare("INSERT INTO twitch_accounts (user_id, login, role, editor_verified) VALUES ('777', 'mod', 'editor', NULL)").run();
  }

  it("downloads through Get Clips Download as a verified editor and streams into R2", async () => {
    await connectEditor();
    let q: URLSearchParams | null = null;
    on(/helix\/clips\/downloads/, (u) => { q = u.searchParams; return ok({ data: [{ clip_id: "tw1", landscape_download_url: "https://media.fixture/tw1.mp4", portrait_download_url: null }] }); });
    on(/media\.fixture/, () => new Response(new Uint8Array(2048), { headers: { "content-length": "2048" } }));
    expect(await fetchMedia(env, 3)).toEqual({ fetched: 1 });
    expect(q!.get("editor_id")).toBe("777");
    expect(q!.get("broadcaster_id")).toBe("494543675");
    expect(q!.getAll("clip_id")).toEqual(["tw1"]);
    expect(env.MEDIA.store.get("clips/c1/source.mp4")?.data.byteLength).toBe(2048);
    const c = env.DB.raw.prepare("SELECT status, rights_basis FROM clips WHERE id = 'c1'").get() as Record<string, string>;
    expect(c.status).toBe("media_ready");
    expect(c.rights_basis).toMatch(/Get Clips Download as editor 'mod'/);
  });

  it("marks the account as not an editor on 403 and stops", async () => {
    await connectEditor();
    on(/helix\/clips\/downloads/, () => Response.json({ message: "not an editor" }, { status: 403 }));
    expect(await fetchMedia(env, 3)).toMatchObject({ blocked: "account is not an editor" });
    expect((env.DB.raw.prepare("SELECT editor_verified v FROM twitch_accounts").get() as { v: number }).v).toBe(0);
    expect(await fetchMedia(env, 3)).toMatchObject({ blocked: "no editor/broadcaster authorization" });
  });
});
