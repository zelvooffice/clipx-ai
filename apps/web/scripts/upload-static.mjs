#!/usr/bin/env node
/**
 * Upload the built static files to R2 (bucket clipx-media, prefix static/) for CLIPX_ASSETS=r2 deployments.
 * Needs CLOUDFLARE_API_TOKEN (R2 edit) and CLOUDFLARE_ACCOUNT_ID. Content-hashed files that already exist are skipped.
 */
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

const ROOT = ".cloudflare/output/v0/workers/default/assets";
const { CLOUDFLARE_API_TOKEN: token, CLOUDFLARE_ACCOUNT_ID: acc } = process.env;
if (!token || !acc) throw new Error("CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are required");
const base = `https://api.cloudflare.com/client/v4/accounts/${acc}/r2/buckets/clipx-media/objects/`;
const auth = { Authorization: `Bearer ${token}` };

async function* walk(dir) {
  for (const e of await readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) yield* walk(p);
    else yield p;
  }
}

const files = [];
for await (const f of walk(ROOT)) {
  const rel = path.relative(ROOT, f).split(path.sep).join("/");
  if (rel === "_headers" || rel.endsWith(".map")) continue;
  files.push(rel);
}
let up = 0, skip = 0;
const queue = [...files];
await Promise.all(Array.from({ length: 6 }, async () => {
  for (let rel = queue.shift(); rel; rel = queue.shift()) {
    const key = `static/${rel}`;
    const hashed = rel.startsWith("_next/static/");
    if (hashed) {
      const h = await fetch(base + encodeURIComponent(key).replace(/%2F/g, "/"), { method: "GET", headers: { ...auth, Range: "bytes=0-0" } });
      if (h.ok) { skip++; await h.body?.cancel(); continue; }
    }
    for (let i = 0; ; i++) {
      const r = await fetch(base + encodeURIComponent(key).replace(/%2F/g, "/"), { method: "PUT", headers: auth, body: await readFile(path.join(ROOT, rel)) });
      if (r.ok) break;
      if (i >= 3) throw new Error(`upload ${key} failed: ${r.status} ${await r.text()}`);
      await new Promise((s) => setTimeout(s, 1000 * 2 ** i));
    }
    up++;
  }
}));
console.log(`static files: ${up} uploaded, ${skip} unchanged (of ${files.length})`);
