/**
 * HMAC request signing for Worker → API calls. Must match services/api/app/security/signing.py:
 *   canonical = "v1\n{ts}\n{nonce}\n{METHOD}\n{path?query}\n{email}\n{sha256_hex(body)}"
 */
const enc = new TextEncoder();

function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(body: Uint8Array<ArrayBuffer>): Promise<string> {
  return hex(await crypto.subtle.digest("SHA-256", body));
}

export function canonical(p: { ts: string; nonce: string; method: string; path: string; email: string; bodyHash: string }) {
  return ["v1", p.ts, p.nonce, p.method.toUpperCase(), p.path, p.email.toLowerCase(), p.bodyHash].join("\n");
}

export async function signRequest(secret: string, method: string, path: string, email: string, body: Uint8Array<ArrayBuffer>) {
  const ts = Math.floor(Date.now() / 1000).toString();
  const nonce = hex(crypto.getRandomValues(new Uint8Array(16)).buffer);
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const msg = canonical({ ts, nonce, method, path, email, bodyHash: await sha256Hex(body) });
  const signature = hex(await crypto.subtle.sign("HMAC", key, enc.encode(msg)));
  return {
    "x-clipx-email": email.toLowerCase(),
    "x-clipx-timestamp": ts,
    "x-clipx-nonce": nonce,
    "x-clipx-signature": signature,
  };
}
