import { bindings, defineConfig, defineWorker } from "cf/config";

/**
 * CLIPX AI web Worker (vinext). Plain configuration comes from the deploy environment (GitHub Actions vars);
 * secrets are declared here and set out-of-band — never committed:
 *   npx wrangler secret put INTERNAL_API_SECRET  (and CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET if used)
 * `nodejs_compat` makes vars and secrets available on process.env (read by lib/env.ts).
 */
const hasCustomDomain = Boolean(process.env.CLIPX_DOMAIN);

export default defineConfig({
  worker: defineWorker({
    name: "clipx-ai",
    entrypoint: "vinext/server/fetch-handler",
    compatibilityDate: "2026-11-11",
    compatibilityFlags: ["nodejs_compat"],
    // Only the Access-protected hostname should serve the dashboard. (The app also fails closed elsewhere,
    // because requests on other hostnames carry no valid Access JWT for this application's AUD.)
    workersDev: !hasCustomDomain,
    previewUrls: false,
    assets: {
      htmlHandling: "none",
      notFoundHandling: "none",
      runWorkerFirst: ["/*", "!/_next/static/*", "!/*/_next/static/*"],
    },
    env: {
      ASSETS: bindings.assets(),
      CLIPX_ENV: bindings.text("production"),
      API_ORIGIN: bindings.text(process.env.API_ORIGIN ?? ""),
      CF_ACCESS_TEAM_DOMAIN: bindings.text(process.env.CF_ACCESS_TEAM_DOMAIN ?? ""),
      CF_ACCESS_AUD: bindings.text(process.env.CF_ACCESS_AUD ?? ""),
      AUTHORIZED_EMAILS: bindings.text(process.env.AUTHORIZED_EMAILS ?? ""),
      INTERNAL_API_SECRET: bindings.secret(),
      CF_ACCESS_CLIENT_ID: bindings.secret(),
      CF_ACCESS_CLIENT_SECRET: bindings.secret(),
    },
  }),
});
