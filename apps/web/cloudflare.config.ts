import { bindings, defineConfig, defineWorker, triggers } from "cf/config";

/**
 * CLIPX AI — everything runs on the Cloudflare free plan inside this one Worker:
 *   D1 (database, auto-provisioned) · R2 (clip media) · Workers AI (Whisper subtitles) · Cron (every minute).
 * Plain configuration comes from the deploy environment (GitHub Actions variables). Secrets are declared here and set
 * out-of-band with `npx wrangler secret put NAME` — never committed.
 */
const hasCustomDomain = Boolean(process.env.CLIPX_DOMAIN);
// Local development runs fully offline: Workers AI has no local emulator, so it is only bound when deployed.
const dev = process.env.CLIPX_ENV === "development";
// CLIPX_ASSETS=r2: deploy without the static-assets upload; the Worker serves static files from R2 (static/<path>),
// uploaded by scripts/upload-static.mjs. Default: Workers static assets.
const assetsInR2 = process.env.CLIPX_ASSETS === "r2";

export default defineConfig({
  worker: defineWorker({
    name: "clipx-ai",
    entrypoint: "./worker.ts",
    compatibilityDate: "2026-11-11",
    compatibilityFlags: ["nodejs_compat"],
    // Only the Access-protected hostname should serve the dashboard (requests elsewhere carry no valid Access JWT
    // for this application's AUD, so the app fails closed anyway).
    workersDev: !hasCustomDomain,
    previewUrls: false,
    assets: assetsInR2 ? undefined : {
      htmlHandling: "none",
      notFoundHandling: "none",
      runWorkerFirst: ["/*", "!/_next/static/*", "!/*/_next/static/*", "!/ffmpeg/*", "!/fonts/*"],
    },
    triggers: [triggers.scheduled({ schedule: "* * * * *" })],
    env: {
      ...(assetsInR2 ? {} : { ASSETS: bindings.assets() }),
      DB: bindings.d1({ name: "clipx" }),
      MEDIA: bindings.r2({ name: "clipx-media" }),
      ...(dev ? {} : { AI: bindings.ai() }),
      // Always "production" when deployed; local dev may set CLIPX_ENV=development (enables DEV_AUTH_EMAIL only there).
      CLIPX_ENV: bindings.text(dev ? "development" : "production"),
      ...(dev ? { DEV_AUTH_EMAIL: bindings.text(process.env.DEV_AUTH_EMAIL ?? "") } : {}),
      // "password": the dashboard password is the only gate (first password needs SETUP_CODE). "access": Cloudflare Access too.
      AUTH_MODE: bindings.text(process.env.AUTH_MODE === "access" ? "access" : "password"),
      SETUP_CODE: bindings.secret(),
      // Access settings and the email allowlist are Worker secrets (set in Cloudflare, never in this public repo).
      CF_ACCESS_TEAM_DOMAIN: bindings.secret(),
      CF_ACCESS_AUD: bindings.secret(),
      AUTHORIZED_EMAILS: bindings.secret(),
      TWITCH_TARGET_LOGIN: bindings.text(process.env.TWITCH_TARGET_LOGIN ?? "lacy"),
      PUBLIC_APP_URL: bindings.text(process.env.PUBLIC_APP_URL ?? ""),
      SESSION_SECRET: bindings.secret(),
      TOKEN_ENCRYPTION_KEY: bindings.secret(),
      TWITCH_CLIENT_ID: bindings.secret(),
      TWITCH_CLIENT_SECRET: bindings.secret(),
      TIKTOK_CLIENT_KEY: bindings.secret(),
      TIKTOK_CLIENT_SECRET: bindings.secret(),
    },
  }),
});
