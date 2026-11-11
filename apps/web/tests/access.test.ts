import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("access gate", () => {
  it("fails closed when Cloudflare Access is not configured", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("CF_ACCESS_TEAM_DOMAIN", "");
    vi.stubEnv("DEV_AUTH_EMAIL", "dev@example.com");
    const { authenticate } = await import("../lib/access");
    const r = await authenticate(new Headers());
    expect(r).toMatchObject({ ok: false, status: 503 });
  });

  it("allows the explicit dev bypass only outside production", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("CF_ACCESS_TEAM_DOMAIN", "");
    vi.stubEnv("DEV_AUTH_EMAIL", "Dev@Example.com");
    const { authenticate } = await import("../lib/access");
    expect(await authenticate(new Headers())).toEqual({ ok: true, email: "dev@example.com", via: "dev" });
  });

  it("rejects requests without an Access token", async () => {
    vi.stubEnv("CF_ACCESS_TEAM_DOMAIN", "https://team.cloudflareaccess.com");
    vi.stubEnv("CF_ACCESS_AUD", "aud");
    vi.stubEnv("AUTHORIZED_EMAILS", "a@b.com");
    const { authenticate } = await import("../lib/access");
    expect(await authenticate(new Headers())).toMatchObject({ ok: false, status: 401 });
    expect(await authenticate(new Headers({ "cf-access-jwt-assertion": "not-a-jwt" }))).toMatchObject({ ok: false, status: 401 });
  });
});
