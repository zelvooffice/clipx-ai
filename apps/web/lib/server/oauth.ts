/// <reference types="@cloudflare/workers-types" />
/** The browser lands here after authorizing on Twitch/TikTok; the code is exchanged server-side (secrets never reach the browser). */
import { authenticate } from "../access";
import { audit } from "./audit";
import { appUrl, type Env } from "./env";
import { ensureSchema } from "./schema";
import * as tiktok from "./tiktok";
import * as twitch from "./twitch";
import * as unlock from "./unlock";

export async function handleOAuth(req: Request, env: Env, provider: "twitch" | "tiktok"): Promise<Response> {
  const auth = await authenticate(req.headers, { team: env.CF_ACCESS_TEAM_DOMAIN, aud: env.CF_ACCESS_AUD,
    emails: env.AUTHORIZED_EMAILS, devEmail: env.DEV_AUTH_EMAIL, production: env.CLIPX_ENV !== "development", mode: env.AUTH_MODE ?? "password" });
  if (!auth.ok) return Response.json({ detail: auth.reason }, { status: auth.status });
  await ensureSchema(env.DB);
  // only an unlocked browser (session cookie from the dashboard password) may complete a connection
  if (!(await unlock.verify(env, unlock.cookieToken(req), auth.email))) return Response.json({ detail: "locked" }, { status: 401 });
  const url = new URL(req.url);
  const p = url.searchParams;
  const dest = new URL("/settings", url.origin);
  dest.searchParams.set("integration", provider);
  const fail = (msg: string) => {
    dest.searchParams.set("result", "error");
    dest.searchParams.set("message", msg.slice(0, 200));
    return Response.redirect(dest.toString(), 302);
  };
  const code = p.get("code");
  const state = p.get("state");
  if (p.get("error") || !code || !state) return fail(p.get("error_description") || p.get("error") || "Authorization was cancelled");
  const base = appUrl(env, url.origin);
  try {
    const r = provider === "twitch"
      ? await twitch.completeConnect(env, code, state, auth.email, `${base}/api/oauth/twitch/callback`)
      : await tiktok.completeConnect(env, code, state, auth.email, `${base}/api/oauth/tiktok/callback/`);
    await audit(env.DB, auth.email, `${provider}.connected`, null, r, req.headers.get("cf-connecting-ip"));
  } catch (e) {
    return fail(e instanceof Error ? e.message : String(e));
  }
  dest.searchParams.set("result", "connected");
  return Response.redirect(dest.toString(), 302);
}
