/** Web Crypto helpers (run natively in Workers; cheap on the 10 ms CPU budget). */
const te = new TextEncoder();
const td = new TextDecoder();

export function b64(u: ArrayBuffer | Uint8Array): string {
  const a = u instanceof Uint8Array ? u : new Uint8Array(u);
  let s = "";
  for (let i = 0; i < a.length; i += 0x8000) s += String.fromCharCode(...a.subarray(i, i + 0x8000));
  return btoa(s);
}

export function unb64(s: string): Uint8Array<ArrayBuffer> {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export const b64url = (u: ArrayBuffer | Uint8Array) => b64(u).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export function randomToken(bytes = 24): string {
  return b64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

async function aesKey(keyB64: string): Promise<CryptoKey> {
  const raw = unb64(keyB64);
  if (raw.length !== 32) throw new Error("TOKEN_ENCRYPTION_KEY must be base64 of 32 bytes");
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

/** AES-256-GCM; the AAD binds a ciphertext to its owner (e.g. "twitch:12345"). Format v1.<iv>.<ct>. */
export async function encrypt(keyB64: string, plaintext: string, aad: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: te.encode(aad) }, await aesKey(keyB64),
    te.encode(plaintext));
  return `v1.${b64url(iv)}.${b64(ct)}`;
}

export async function decrypt(keyB64: string, token: string, aad: string): Promise<string> {
  const [v, ivs, cts] = token.split(".");
  if (v !== "v1" || !ivs || !cts) throw new Error("malformed ciphertext");
  const iv = unb64(ivs.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (ivs.length % 4)) % 4));
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv, additionalData: te.encode(aad) }, await aesKey(keyB64),
    unb64(cts));
  return td.decode(pt);
}

export async function hmacHex(secret: string, msg: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", te.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return [...new Uint8Array(await crypto.subtle.sign("HMAC", key, te.encode(msg)))]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

/** PBKDF2-SHA256. Format: pbkdf2:sha256:<iterations>:<salt b64>:<hash b64>. Iterations are sized for the
 *  Workers free plan CPU budget; online guessing is additionally capped by the lockout (5 tries / 15 min) and the
 *  hash itself is a Worker secret. */
export async function hashPassword(password: string, iterations = 25000): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const dk = await pbkdf2(password, salt, iterations);
  return `pbkdf2:sha256:${iterations}:${b64(salt)}:${b64(dk)}`;
}

async function pbkdf2(password: string, salt: Uint8Array<ArrayBuffer>, iterations: number): Promise<ArrayBuffer> {
  const key = await crypto.subtle.importKey("raw", te.encode(password), "PBKDF2", false, ["deriveBits"]);
  return crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, 256);
}

export async function verifyPassword(password: string, encoded: string): Promise<boolean> {
  const [algo, hash, it, saltB64, dkB64] = encoded.split(":");
  if (algo !== "pbkdf2" || hash !== "sha256" || !it || !saltB64 || !dkB64) return false;
  const dk = b64(await pbkdf2(password, unb64(saltB64), Number(it)));
  return safeEqual(dk, dkB64);
}
