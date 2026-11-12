/// <reference types="@cloudflare/workers-types" />
/**
 * Twitch OAuth + Helix (verified against dev.twitch.tv/docs/api/reference, 2026-11-11):
 *  - Get Streams (live detection), Get Clips (viewer clips: view_count, vod_offset, duration),
 *  - Get Clips Download (editor:manage:clips | channel:manage:clips) for authorized clip media.
 * Tokens are AES-GCM encrypted in D1; user tokens are validated at least hourly as Twitch requires.
 */
import { decrypt, encrypt, randomToken } from "./crypto";
import { all, j, now, one, run, s } from "./db";
import { type Env, targetLogin } from "./env";

const HELIX = "https://api.twitch.tv/helix";
const ID = "https://id.twitch.tv/oauth2";

export const SCOPES: Record<string, string[]> = {
  operator: ["clips:edit"],
  editor: ["clips:edit", "editor:manage:clips"],
  broadcaster: ["clips:edit", "channel:manage:clips"],
};

export class TwitchError extends Error {
  constructor(public status: number, message: string) {
    super(`Twitch ${status}: ${message}`);
  }
}

export interface TokenSet {
  access_token: string;
  refresh_token?: string | null;
  expires_in?: number | null;
  scopes: string[];
}

export const configured = (env: Env) => Boolean(env.TWITCH_CLIENT_ID && env.TWITCH_CLIENT_SECRET);

export function authorizeUrl(env: Env, state: string, role: string, redirectUri: string): string {
  const q = new URLSearchParams({ client_id: env.TWITCH_CLIENT_ID ?? "", redirect_uri: redirectUri, response_type: "code",
    scope: (SCOPES[role] ?? SCOPES.operator).join(" "), state, force_verify: "true" });
  return `${ID}/authorize?${q}`;
}

async function tokenRequest(env: Env, body: Record<string, string>): Promise<TokenSet> {
  const r = await fetch(`${ID}/token`, { method: "POST", body: new URLSearchParams({
    client_id: env.TWITCH_CLIENT_ID ?? "", client_secret: env.TWITCH_CLIENT_SECRET ?? "", ...body }) });
  const d = (await r.json().catch(() => ({}))) as Record<string, unknown>;
  if (!r.ok || !d.access_token) throw new TwitchError(r.status, String(d.message ?? "token request failed"));
  const scope = d.scope;
  return { access_token: String(d.access_token), refresh_token: (d.refresh_token as string) ?? null,
           expires_in: (d.expires_in as number) ?? null, scopes: Array.isArray(scope) ? scope.map(String) : [] };
}

export const exchangeCode = (env: Env, code: string, redirectUri: string) =>
  tokenRequest(env, { code, grant_type: "authorization_code", redirect_uri: redirectUri });

export async function validate(token: string): Promise<{ user_id: string; login: string; scopes: string[] } | null> {
  const r = await fetch(`${ID}/validate`, { headers: { Authorization: `OAuth ${token}` } });
  if (r.status === 401) return null;
  if (!r.ok) throw new TwitchError(r.status, "validate failed");
  return (await r.json()) as { user_id: string; login: string; scopes: string[] };
}

// ------------------------------------------------------------------------------------- token storage
export async function storeToken(env: Env, provider: string, subject: string, t: TokenSet,
                                 refreshExpiresIn?: number | null): Promise<string> {
  const id = `${provider}:${subject}`;
  const key = env.TOKEN_ENCRYPTION_KEY ?? "";
  const access = await encrypt(key, t.access_token, id);
  const refresh = t.refresh_token ? await encrypt(key, t.refresh_token, id) : null;
  await run(env.DB, `INSERT INTO tokens (id, provider, subject, access_enc, refresh_enc, scopes, expires_at, refresh_expires_at,
      validated_at, revoked, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
    ON CONFLICT(id) DO UPDATE SET access_enc = excluded.access_enc, refresh_enc = COALESCE(excluded.refresh_enc, tokens.refresh_enc),
      scopes = excluded.scopes, expires_at = excluded.expires_at, refresh_expires_at = COALESCE(excluded.refresh_expires_at, tokens.refresh_expires_at),
      validated_at = excluded.validated_at, revoked = 0, updated_at = excluded.updated_at`,
    id, provider, subject, access, refresh, s(t.scopes), t.expires_in ? now() + t.expires_in : null,
    refreshExpiresIn ? now() + refreshExpiresIn : null, now(), now());
  return id;
}

interface TokenRow { id: string; access_enc: string; refresh_enc: string | null; scopes: string; expires_at: number | null;
  validated_at: number | null; revoked: number }

export async function readToken(env: Env, id: string): Promise<{ row: TokenRow; access: string } | null> {
  const row = await one<TokenRow>(env.DB, "SELECT * FROM tokens WHERE id = ?", id);
  if (!row || row.revoked) return null;
  return { row, access: await decrypt(env.TOKEN_ENCRYPTION_KEY ?? "", row.access_enc, id) };
}

export class NeedsReconnect extends Error {}

/** A valid user token for a connected Twitch account (refreshes near expiry, validates hourly). */
export async function userToken(env: Env, userId: string): Promise<string> {
  const id = `twitch:${userId}`;
  const t = await readToken(env, id);
  if (!t) throw new NeedsReconnect("Twitch account needs to be reconnected");
  const expSoon = t.row.expires_at !== null && t.row.expires_at - now() < 300;
  const stale = !t.row.validated_at || now() - t.row.validated_at > 3600;
  if (!expSoon && !stale) return t.access;
  if (!expSoon) {
    const info = await validate(t.access);
    if (info) {
      await run(env.DB, "UPDATE tokens SET validated_at = ?, scopes = ? WHERE id = ?", now(), s(info.scopes), id);
      return t.access;
    }
  }
  if (!t.row.refresh_enc) {
    await run(env.DB, "UPDATE tokens SET revoked = 1 WHERE id = ?", id);
    throw new NeedsReconnect("Twitch token expired");
  }
  try {
    const fresh = await tokenRequest(env, { grant_type: "refresh_token",
      refresh_token: await decrypt(env.TOKEN_ENCRYPTION_KEY ?? "", t.row.refresh_enc, id) });
    await storeToken(env, "twitch", userId, fresh);
    return fresh.access_token;
  } catch (e) {
    if (e instanceof TwitchError && (e.status === 400 || e.status === 401)) {
      await run(env.DB, "UPDATE tokens SET revoked = 1 WHERE id = ?", id);
      throw new NeedsReconnect("Twitch authorization was revoked; reconnect");
    }
    throw e;
  }
}

async function cached(env: Env, key: string): Promise<string | null> {
  const r = await one<{ value: string; expires_at: number }>(env.DB, "SELECT value, expires_at FROM cache WHERE key = ?", key);
  return r && r.expires_at > now() ? r.value : null;
}

async function putCache(env: Env, key: string, value: string, ttl: number) {
  await run(env.DB, "INSERT INTO cache (key, value, expires_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at",
    key, value, now() + ttl);
}

export async function appToken(env: Env): Promise<string> {
  const c = await cached(env, "twitch:app_token");
  if (c) return decrypt(env.TOKEN_ENCRYPTION_KEY ?? "", c, "twitch:app");
  const t = await tokenRequest(env, { grant_type: "client_credentials" });
  await putCache(env, "twitch:app_token", await encrypt(env.TOKEN_ENCRYPTION_KEY ?? "", t.access_token, "twitch:app"),
    Math.max(60, (t.expires_in ?? 3600) - 300));
  return t.access_token;
}

// ------------------------------------------------------------------------------------- Helix
export async function helix<T = Record<string, unknown>>(env: Env, token: string, path: string,
    params: [string, string][] = [], method = "GET"): Promise<{ status: number; body: T }> {
  const url = `${HELIX}${path}${params.length ? `?${new URLSearchParams(params)}` : ""}`;
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await fetch(url, { method, headers: { Authorization: `Bearer ${token}`, "Client-Id": env.TWITCH_CLIENT_ID ?? "" } });
    if ((r.status === 429 || r.status >= 500) && attempt === 0) continue;
    const body = (await r.json().catch(() => ({}))) as T;
    if (!r.ok) throw new TwitchError(r.status, String((body as Record<string, unknown>)?.message ?? r.statusText));
    return { status: r.status, body };
  }
  throw new TwitchError(503, "unreachable");
}

export interface TwitchUser { id: string; login: string; display_name: string; profile_image_url?: string }

export async function target(env: Env): Promise<TwitchUser> {
  const key = `twitch:user:${targetLogin(env)}`;
  const c = await cached(env, key);
  if (c) return JSON.parse(c) as TwitchUser;
  const { body } = await helix<{ data: TwitchUser[] }>(env, await appToken(env), "/users", [["login", targetLogin(env)]]);
  if (!body.data?.length) throw new TwitchError(404, `Twitch user '${targetLogin(env)}' not found`);
  await putCache(env, key, JSON.stringify(body.data[0]), 6 * 3600);
  return body.data[0];
}

export interface Stream { id: string; user_id: string; title: string; game_name: string; started_at: string; type: string }

export async function liveStream(env: Env, userId: string): Promise<Stream | null> {
  const { body } = await helix<{ data: Stream[] }>(env, await appToken(env), "/streams", [["user_id", userId], ["type", "live"]]);
  return body.data?.[0] ?? null;
}

export interface TwitchClip { id: string; url: string; title: string; creator_name: string; view_count: number;
  created_at: string; duration: number; vod_offset: number | null; video_id: string }

export async function clipsBetween(env: Env, broadcasterId: string, startedAt: number, endedAt: number,
                                   pages = 3): Promise<TwitchClip[]> {
  const out: TwitchClip[] = [];
  let after: string | undefined;
  const iso = (t: number) => new Date(t * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  for (let i = 0; i < pages; i++) {
    const params: [string, string][] = [["broadcaster_id", broadcasterId], ["started_at", iso(startedAt)],
      ["ended_at", iso(endedAt)], ["first", "100"]];
    if (after) params.push(["after", after]);
    const { body } = await helix<{ data: TwitchClip[]; pagination?: { cursor?: string } }>(env, await appToken(env), "/clips", params);
    out.push(...(body.data ?? []));
    after = body.pagination?.cursor;
    if (!after) break;
  }
  return out;
}

// ------------------------------------------------------------------------------------- accounts
export interface Account { user_id: string; login: string; role: string; editor_verified: number | null }

export async function accounts(env: Env): Promise<(Account & { scopes: string[]; revoked: boolean })[]> {
  const rows = await all<Account & { scopes: string | null; revoked: number | null }>(env.DB,
    `SELECT a.*, t.scopes, t.revoked FROM twitch_accounts a LEFT JOIN tokens t ON t.id = 'twitch:' || a.user_id
     ORDER BY a.created_at`);
  return rows.map((r) => ({ ...r, scopes: j<string[]>(r.scopes, []), revoked: !r.scopes || Boolean(r.revoked) }));
}

export async function mediaAccount(env: Env): Promise<Account | null> {
  const list = (await accounts(env)).filter((a) => !a.revoked && ["editor", "broadcaster"].includes(a.role)
    && a.editor_verified !== 0 && a.scopes.some((x) => x === "editor:manage:clips" || x === "channel:manage:clips"));
  list.sort((a, b) => Number(a.role !== "broadcaster") - Number(b.role !== "broadcaster"));
  return list[0] ?? null;
}

export async function capabilities(env: Env) {
  const accts = (await accounts(env)).filter((a) => !a.revoked);
  const media = await mediaAccount(env);
  const notes: string[] = [];
  if (!configured(env)) notes.push("Twitch app credentials are not configured (TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET).");
  if (!media) notes.push("No broadcaster/editor authorization: clip media can't be downloaded through an authorized " +
    "method, so clips can be listed but not edited. Ask Lacy or a channel editor to connect.");
  const how = "Free plan: moments come from clips Lacy's viewers create on Twitch, ranked by views. There is no " +
    "real-time audio/video analysis; video is edited in an open dashboard tab.";
  return { app_configured: configured(env), accounts_connected: accts.length, can_detect_live: configured(env),
           can_list_clips: configured(env), can_download_clips: Boolean(media), media_account: media?.login ?? null, notes, how };
}

export async function startConnect(env: Env, role: string, email: string, redirectUri: string): Promise<string> {
  if (!SCOPES[role]) throw new Error("role must be operator, editor or broadcaster");
  if (!configured(env)) throw new Error("Twitch app credentials are not configured");
  const state = randomToken(32);
  await run(env.DB, "INSERT INTO oauth_states (state, provider, role, created_by, expires_at) VALUES (?, 'twitch', ?, ?, ?)",
    state, role, email, now() + 600);
  return authorizeUrl(env, state, role, redirectUri);
}

export async function consumeState(env: Env, provider: string, state: string, email: string) {
  const row = await one<{ provider: string; role: string | null; created_by: string; expires_at: number }>(env.DB,
    "SELECT * FROM oauth_states WHERE state = ?", state);
  await run(env.DB, "DELETE FROM oauth_states WHERE state = ?", state); // single use
  if (!row || row.provider !== provider) throw new Error("unknown OAuth state");
  if (row.expires_at < now()) throw new Error("OAuth state expired, start again");
  if (row.created_by !== email) throw new Error("OAuth state belongs to a different user");
  return row;
}

export async function completeConnect(env: Env, code: string, state: string, email: string, redirectUri: string) {
  const st = await consumeState(env, "twitch", state, email);
  const t = await exchangeCode(env, code, redirectUri);
  const info = await validate(t.access_token);
  if (!info) throw new Error("Twitch returned an invalid token");
  const tgt = await target(env);
  const role = info.user_id === tgt.id ? "broadcaster" : st.role === "editor" ? "editor" : "operator";
  await storeToken(env, "twitch", info.user_id, { ...t, scopes: info.scopes });
  await run(env.DB, `INSERT INTO twitch_accounts (user_id, login, role, editor_verified, connected_by, created_at)
    VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET login = excluded.login, role = excluded.role,
    editor_verified = excluded.editor_verified, connected_by = excluded.connected_by`,
    info.user_id, info.login, role, role === "broadcaster" ? 1 : null, email, now());
  return { user_id: info.user_id, login: info.login, role };
}
