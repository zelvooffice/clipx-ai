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
    assets: {
      htmlHandling: "none",
      notFoundHandling: "none",
      runWorkerFirst: ["/*", "!/_next/static/*", "!/*/_next/static/*", "!/ffmpeg/*", "!/fonts/*"],
    },
    triggers: [triggers.scheduled({ schedule: "* * * * *" })],
    env: {
      ASSETS: bindings.assets(),
      DB: bindings.d1({ name: "clipx" }),
      MEDIA: bindings.r2({ name: "clipx-media" }),
      ...(dev ? {} : { AI: bindings.ai() }),
      // Always "production" when deployed; local dev may set CLIPX_ENV=development (enables DEV_AUTH_EMAIL only there).
      CLIPX_ENV: bindings.text(dev ? "development" : "production"),
      ...(dev ? { DEV_AUTH_EMAIL: bindings.text(process.env.DEV_AUTH_EMAIL ?? "") } : {}),
      CF_ACCESS_TEAM_DOMAIN: bindings.text(process.env.CF_ACCESS_TEAM_DOMAIN ?? ""),
      CF_ACCESS_AUD: bindings.text(process.env.CF_ACCESS_AUD ?? ""),
      AUTHORIZED_EMAILS: bindings.text(process.env.AUTHORIZED_EMAILS ?? ""),
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
