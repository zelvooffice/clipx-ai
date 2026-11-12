/// <reference types="@cloudflare/workers-types" />
/** Per-visit dashboard password: every tab must unlock, for every user. Tokens are HMAC-signed, bound to the email
 *  and registered in D1 so "Lock" revokes them immediately. 5 wrong passwords -> 15 minute lockout. */
import { now, one, run } from "./db";
import { hashPassword, hmacHex, randomToken, safeEqual, verifyPassword } from "./crypto";
import type { Env } from "./env";

export const MAX_ATTEMPTS = 5;
export const LOCKOUT_S = 15 * 60;
export const TTL_S = 8 * 3600;

export const MIN_PASSWORD = 12;
const HASH_KEY = "dashboard_password_hash";

/** The password is always required. Its hash is a Worker secret, or — when the owner chose it on first visit — in D1. */
export async function passwordHash(env: Env): Promise<string | null> {
  if (env.DASHBOARD_PASSWORD_HASH?.startsWith("pbkdf2:")) return env.DASHBOARD_PASSWORD_HASH;
  const r = await one<{ value: string }>(env.DB, "SELECT value FROM settings WHERE key = ?", HASH_KEY);
  return r?.value ?? null;
}

/** Whether the password can be changed from the dashboard (not when it is pinned by the Worker secret). */
export const managedInDashboard = (env: Env) => !env.DASHBOARD_PASSWORD_HASH?.startsWith("pbkdf2:");

/** Whether creating the first password needs the one-time SETUP_CODE (always, when the site is not behind Access). */
export const setupNeedsCode = (env: Env) => (env.AUTH_MODE ?? "password") === "password";

/** First visit: the owner creates the password. Only once. Without Cloudflare Access the one-time SETUP_CODE
 *  (a Worker secret) is required, so a stranger who finds the site first cannot claim it. */
export async function setup(env: Env, email: string, password: string, code?: string) {
  if (await passwordHash(env)) return { ok: false as const, status: 409, detail: "A password already exists." };
  if (setupNeedsCode(env)) {
    const want = env.SETUP_CODE ?? "";
    if (want.length < 16 || !code || !safeEqual(await hmacHex(secret(env), code), await hmacHex(secret(env), want))) {
      return { ok: false as const, status: 403, detail: "Wrong setup code." };
    }
  }
  if (password.length < MIN_PASSWORD) return { ok: false as const, status: 422, detail: `Use at least ${MIN_PASSWORD} characters.` };
  const n = await run(env.DB, "INSERT INTO settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(key) DO NOTHING",
    HASH_KEY, await hashPassword(password), email, now());
  if (!n) return { ok: false as const, status: 409, detail: "A password already exists." };
  return { ok: true as const, ...(await issue(env, email)) };
}

/** Change it (requires the current password). Every other open session is signed out. */
export async function change(env: Env, email: string, current: string, next: string) {
  if (!managedInDashboard(env)) return { ok: false as const, status: 409, detail: "The password is set as a Cloudflare secret (DASHBOARD_PASSWORD_HASH); change it there." };
  const h = await passwordHash(env);
  if (!h || !(await verifyPassword(current, h))) return { ok: false as const, status: 401, detail: "The current password is wrong." };
  if (next.length < MIN_PASSWORD) return { ok: false as const, status: 422, detail: `Use at least ${MIN_PASSWORD} characters.` };
  await run(env.DB, "UPDATE settings SET value = ?, updated_by = ?, updated_at = ? WHERE key = ?", await hashPassword(next), email, now(), HASH_KEY);
  await run(env.DB, "DELETE FROM unlock_sessions");
  return { ok: true as const, ...(await issue(env, email)) };
}

const secret = (env: Env) => `${env.SESSION_SECRET ?? ""}:unlock-v1`;

export async function issue(env: Env, email: string): Promise<{ token: string; expires_at: number }> {
  const sid = randomToken(18);
  const exp = now() + TTL_S;
  await run(env.DB, "INSERT INTO unlock_sessions (sid, email, expires_at) VALUES (?, ?, ?)", sid, email, exp);
  await run(env.DB, "DELETE FROM unlock_sessions WHERE expires_at < ?", now());
  const payload = `v1.${sid}.${exp}`;
  return { token: `${payload}.${await hmacHex(secret(env), `${payload}.${email}`)}`, expires_at: exp };
}

export async function verify(env: Env, token: string | null, email: string): Promise<boolean> {
  if (!token || token.length > 300) return false;
  const parts = token.split(".");
  if (parts.length !== 4 || parts[0] !== "v1") return false;
  const [, sid, exp, sig] = parts;
  if (!/^\d+$/.test(exp) || Number(exp) < now()) return false;
  if (!safeEqual(await hmacHex(secret(env), `v1.${sid}.${exp}.${email}`), sig)) return false;
  const row = await one<{ email: string }>(env.DB, "SELECT email FROM unlock_sessions WHERE sid = ? AND expires_at > ?", sid, now());
  return row?.email === email;
}

/** HttpOnly cookie carrying the same unlock token, for <video>/<img> media and the OAuth return. It has no
 *  Max-Age, so it ends when the browser closes; API calls still need the per-tab header. */
export const COOKIE = "clipx_s";
export const setCookie = (token: string) => `${COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Lax`;
export const clearCookie = () => `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
export const cookieToken = (req: Request) => new RegExp(`(?:^|;\\s*)${COOKIE}=([^;]+)`).exec(req.headers.get("cookie") ?? "")?.[1] ?? null;

export async function revoke(env: Env, token: string | null): Promise<void> {
  const sid = token?.split(".")[1];
  if (sid) await run(env.DB, "DELETE FROM unlock_sessions WHERE sid = ?", sid);
}

export async function attemptsLeft(env: Env, email: string): Promise<number> {
  const r = await one<{ count: number; window_start: number }>(env.DB,
    "SELECT count, window_start FROM unlock_failures WHERE email = ?", email);
  if (!r || r.window_start + LOCKOUT_S < now()) return MAX_ATTEMPTS;
  return Math.max(0, MAX_ATTEMPTS - r.count);
}

const GLOBAL_KEY = "*global*";
const GLOBAL_MAX = 30;

export const recordFailure = (env: Env, key: string) => bump(env, key);

async function bump(env: Env, key: string) {
  const r = await one<{ count: number; window_start: number }>(env.DB, "SELECT count, window_start FROM unlock_failures WHERE email = ?", key);
  const fresh = !r || r.window_start + LOCKOUT_S < now();
  const count = fresh ? 1 : r!.count + 1;
  await run(env.DB, `INSERT INTO unlock_failures (email, count, window_start) VALUES (?, 1, ?)
    ON CONFLICT(email) DO UPDATE SET count = ?, window_start = ?`, key, now(), count, fresh ? now() : r!.window_start);
  return count;
}

/** Wrong-password limits: 5 per `limitKey` (the email behind Access, otherwise the client IP) and 30 for the whole
 *  site per 15 minutes. */
export async function tryUnlock(env: Env, email: string, password: string, limitKey: string = email):
  Promise<{ ok: true; token: string; expires_at: number } | { ok: false; status: 401 | 429; detail: string }> {
  const g = await one<{ count: number; window_start: number }>(env.DB, "SELECT count, window_start FROM unlock_failures WHERE email = ?", GLOBAL_KEY);
  if ((await attemptsLeft(env, limitKey)) <= 0 || (g && g.window_start + LOCKOUT_S >= now() && g.count >= GLOBAL_MAX)) {
    return { ok: false, status: 429, detail: "Too many wrong passwords. Try again in 15 minutes." };
  }
  const h = await passwordHash(env);
  if (!h) return { ok: false, status: 401, detail: "No password has been created yet." };
  if (!(await verifyPassword(password, h))) {
    const count = await bump(env, limitKey);
    await bump(env, GLOBAL_KEY);
    const left = Math.max(0, MAX_ATTEMPTS - count);
    return { ok: false, status: 401, detail: `Wrong password. ${left} attempt${left === 1 ? "" : "s"} left.` };
  }
  await run(env.DB, "DELETE FROM unlock_failures WHERE email = ?", limitKey);
  return { ok: true, ...(await issue(env, email)) };
}
