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
    return (vinext as unknown as ExportedHandler<Env>).fetch!(req as never, env, ctx);
  },
  async scheduled(_ev: ScheduledController, env: Env, ctx: ExecutionContext) {
    ctx.waitUntil(runScheduled(env).then(() => undefined, (e) => console.error("cron failed", e)));
  },
} satisfies ExportedHandler<Env>;
