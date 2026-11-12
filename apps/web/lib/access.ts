import { createRemoteJWKSet, jwtVerify } from "jose";
import { env, isProduction } from "./env";

/**
 * Cloudflare Access verification (https://developers.cloudflare.com/cloudflare-one/ → Validate JWTs):
 * token from the `Cf-Access-Jwt-Assertion` header, keys from <team>/cdn-cgi/access/certs, issuer = team
 * domain, audience = the application's AUD tag. The email must also be on AUTHORIZED_EMAILS.
 * Fails closed: if Access is not configured the dashboard is unreachable (except an explicit local dev bypass).
 */
export type AuthResult =
  | { ok: true; email: string; via: "access" | "dev" }
  | { ok: false; status: 401 | 403 | 503; reason: string };

let jwks: ReturnType<typeof createRemoteJWKSet> | null = null;
let jwksFor = "";

function allowlist(list?: string): Set<string> {
  return new Set(
    (list ?? env("AUTHORIZED_EMAILS"))
      .split(",")
      .map((e) => e.trim().toLowerCase())
      .filter(Boolean),
  );
}

export interface AccessConfig {
  team?: string;
  aud?: string;
  emails?: string;
  devEmail?: string;
  production?: boolean;
}

export async function authenticate(headers: Headers, cfg: AccessConfig = {}): Promise<AuthResult> {
  const team = (cfg.team ?? env("CF_ACCESS_TEAM_DOMAIN")).replace(/\/$/, "");
  const aud = cfg.aud ?? env("CF_ACCESS_AUD");
  const devEmail = cfg.devEmail ?? env("DEV_AUTH_EMAIL");
  const production = cfg.production ?? isProduction();

  if (!team || !aud) {
    if (devEmail && !production) return { ok: true, email: devEmail.toLowerCase(), via: "dev" };
    return { ok: false, status: 503, reason: "Authentication is not configured (CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_AUD)." };
  }
  const token = headers.get("cf-access-jwt-assertion");
  if (!token) return { ok: false, status: 401, reason: "Missing Cloudflare Access token." };
  if (!jwks || jwksFor !== team) {
    jwks = createRemoteJWKSet(new URL(`${team}/cdn-cgi/access/certs`));
    jwksFor = team;
  }
  try {
    const { payload } = await jwtVerify(token, jwks, { issuer: team, audience: aud });
    const email = String(payload.email ?? "").toLowerCase();
    if (!email) return { ok: false, status: 403, reason: "Service tokens cannot open the dashboard." };
    const allowed = allowlist(cfg.emails);
    if (allowed.size === 0) return { ok: false, status: 503, reason: "AUTHORIZED_EMAILS is not configured." };
    if (!allowed.has(email)) return { ok: false, status: 403, reason: "This email is not authorized." };
    return { ok: true, email, via: "access" };
  } catch {
    return { ok: false, status: 401, reason: "Invalid or expired Cloudflare Access token." };
  }
}
