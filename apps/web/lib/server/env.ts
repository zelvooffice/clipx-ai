/// <reference types="@cloudflare/workers-types" />
/** Bindings and configuration available to the Worker (free plan: Workers + D1 + R2 + Workers AI + Cron). */
export interface Env {
  DB: D1Database;
  MEDIA: R2Bucket;
  AI?: Ai;
  ASSETS?: Fetcher; // Workers static assets; absent when static files are served from R2 (static/<path>)
  // vars
  AUTHORIZED_EMAILS?: string;
  CF_ACCESS_TEAM_DOMAIN?: string;
  CF_ACCESS_AUD?: string;
  TWITCH_TARGET_LOGIN?: string;
  PUBLIC_APP_URL?: string;
  CLIPX_ENV?: string;
  AUTH_MODE?: string; // "password" (default): the dashboard password alone; "access": Cloudflare Access + password
  SETUP_CODE?: string; // one-time code required to create the first password when AUTH_MODE is "password"
  DEV_AUTH_EMAIL?: string; // local development only (ignored when CLIPX_ENV is production)
  // secrets
  SESSION_SECRET?: string;
  TOKEN_ENCRYPTION_KEY?: string;
  DASHBOARD_PASSWORD_HASH?: string; // optional override; normally the owner creates the password on first visit (D1)
  TWITCH_CLIENT_ID?: string;
  TWITCH_CLIENT_SECRET?: string;
  TIKTOK_CLIENT_KEY?: string;
  TIKTOK_CLIENT_SECRET?: string;
}

export function targetLogin(env: Env): string {
  return (env.TWITCH_TARGET_LOGIN || "lacy").toLowerCase();
}

export function appUrl(env: Env, fallback?: string): string {
  return (env.PUBLIC_APP_URL || fallback || "").replace(/\/$/, "");
}

export function configProblems(env: Env): string[] {
  const p: string[] = [];
  if (!env.SESSION_SECRET || env.SESSION_SECRET.length < 32) p.push("SESSION_SECRET (>= 32 chars) is not set");
  if (!env.TOKEN_ENCRYPTION_KEY) p.push("TOKEN_ENCRYPTION_KEY is not set");
  return p;
}
