/// <reference types="@cloudflare/workers-types" />
/**
 * Worker entry: the JSON API and OAuth callbacks are served directly (they need the D1/R2/AI bindings); everything
 * else (pages, assets) goes to vinext. The cron trigger drives live detection, clip sync, media fetch, publishing
 * and analytics.
 */
import vinext from "vinext/server/fetch-handler";
import { handleApi } from "./lib/server/api";
import { runScheduled } from "./lib/server/cron";
import type { Env } from "./lib/server/env";
import { handleOAuth } from "./lib/server/oauth";

const SECURITY_HEADERS: Record<string, string> = {
  "x-frame-options": "DENY", "x-content-type-options": "nosniff", "referrer-policy": "same-origin",
  "strict-transport-security": "max-age=31536000; includeSubDomains",
};

const TYPES: Record<string, string> = {
  js: "text/javascript; charset=utf-8", mjs: "text/javascript; charset=utf-8", css: "text/css; charset=utf-8",
  json: "application/json", svg: "image/svg+xml", png: "image/png", ico: "image/x-icon", woff2: "font/woff2",
  otf: "font/otf", gz: "application/gzip", txt: "text/plain; charset=utf-8", map: "application/json", html: "text/html; charset=utf-8",
};

/**
 * Static files from R2 (`static/<path>`), used when the Worker is deployed without the Workers static-assets
 * upload. Behaves like the ASSETS binding for vinext: 404 when missing.
 */
function r2Assets(env: Env): Fetcher {
  return {
    async fetch(input: RequestInfo | URL) {
      const url = new URL(input instanceof Request ? input.url : String(input));
      const key = `static${decodeURIComponent(url.pathname)}`;
      if (key.includes("..")) return new Response("not found", { status: 404 });
      const obj = await env.MEDIA.get(key);
      if (!obj) return new Response("not found", { status: 404 });
      const ext = key.split(".").pop() ?? "";
      return new Response(obj.body, { headers: { "content-type": TYPES[ext] ?? "application/octet-stream",
        "content-length": String(obj.size), etag: obj.httpEtag,
        "cache-control": key.startsWith("static/_next/static/") ? "public, max-age=31536000, immutable" : "public, max-age=3600" } });
    },
    connect() { throw new Error("not supported"); },
  } as unknown as Fetcher;
}

const STATIC = /^\/(?:_next\/static|[^/]+\/_next\/static|ffmpeg|fonts)\//;

function secure(res: Response) {
  const r = new Response(res.body, res);
  for (const [k, v] of Object.entries(SECURITY_HEADERS)) r.headers.set(k, v);
  return r;
}

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const { pathname } = new URL(req.url);
    if (pathname.startsWith("/api/v1/")) return secure(await handleApi(req, env, pathname.slice("/api/v1/".length)));
    const m = /^\/api\/oauth\/(twitch|tiktok)\/callback\/?$/.exec(pathname);
    if (m) return secure(await handleOAuth(req, env, m[1] as "twitch" | "tiktok"));
    const e = env.ASSETS ? env : ({ ...env, ASSETS: r2Assets(env) } as Env);
    if (!env.ASSETS && STATIC.test(pathname) && (req.method === "GET" || req.method === "HEAD")) return e.ASSETS!.fetch(req);
    return (vinext as unknown as ExportedHandler<Env>).fetch!(req as never, e, ctx);
  },
  async scheduled(_ev: ScheduledController, env: Env, ctx: ExecutionContext) {
    ctx.waitUntil(runScheduled(env).then(() => undefined, (e) => console.error("cron failed", e)));
  },
} satisfies ExportedHandler<Env>;
