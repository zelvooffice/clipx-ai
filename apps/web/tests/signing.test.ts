import { createHmac } from "node:crypto";
import { describe, expect, it } from "vitest";
import { canonical, sha256Hex, signRequest } from "../lib/signing";

// Golden vector produced by services/api/app/security/signing.py — the Worker and the API must agree byte-for-byte.
const PY_SIGNATURE = "c841fa5e8e2a47fe7508a5bd5272f3046fb7d9dc5fd9b812ccbd3a716bfeb505";

describe("request signing", () => {
  it("matches the Python implementation", async () => {
    const body = new TextEncoder().encode('{"a":1}');
    const msg = canonical({ ts: "1762862400", nonce: "00112233445566778899aabbccddeeff", method: "post",
      path: "/v1/clips/abc?x=1", email: "Owner@Example.com", bodyHash: await sha256Hex(body) });
    const sig = createHmac("sha256", "golden-secret-0123456789abcdef0123456789").update(msg).digest("hex");
    expect(sig).toBe(PY_SIGNATURE);
  });

  it("produces fresh headers that verify", async () => {
    const body = new TextEncoder().encode("{}");
    const h = await signRequest("s".repeat(40), "PUT", "/v1/settings/processing", "A@B.com", body);
    expect(h["x-clipx-email"]).toBe("a@b.com");
    expect(h["x-clipx-nonce"]).toMatch(/^[0-9a-f]{32}$/);
    const msg = canonical({ ts: h["x-clipx-timestamp"], nonce: h["x-clipx-nonce"], method: "PUT",
      path: "/v1/settings/processing", email: "a@b.com", bodyHash: await sha256Hex(body) });
    expect(h["x-clipx-signature"]).toBe(createHmac("sha256", "s".repeat(40)).update(msg).digest("hex"));
  });
});
