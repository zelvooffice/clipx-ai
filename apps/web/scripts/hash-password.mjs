#!/usr/bin/env node
/**
 * Prints the DASHBOARD_PASSWORD_HASH for a password (PBKDF2-SHA256, same format the Worker verifies).
 * Usage:  node scripts/hash-password.mjs            (prompts, input hidden)
 * Then:   npx wrangler secret put DASHBOARD_PASSWORD_HASH   and paste the printed value.
 */
import { webcrypto as crypto } from "node:crypto";
import readline from "node:readline";

const ITERATIONS = 25000;

async function hash(pw) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: ITERATIONS }, key, 256);
  const b64 = (u) => Buffer.from(u).toString("base64");
  return `pbkdf2:sha256:${ITERATIONS}:${b64(salt)}:${b64(new Uint8Array(bits))}`;
}

function ask(q) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true });
  rl._writeToOutput = (s) => rl.output.write(s.includes(q) ? s : "");
  return new Promise((r) => rl.question(q, (a) => { rl.close(); process.stdout.write("\n"); r(a); }));
}

const pw = process.env.CLIPX_PASSWORD ?? (await ask("Dashboard password (min 12 chars): "));
if (!pw || pw.length < 12) {
  console.error("Use at least 12 characters.");
  process.exit(1);
}
console.log(await hash(pw));
