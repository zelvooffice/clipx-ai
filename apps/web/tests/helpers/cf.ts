/** Test doubles for Cloudflare bindings: D1 on node:sqlite (same SQLite dialect) and an in-memory R2 bucket. */
import { DatabaseSync } from "node:sqlite";

const norm = (v: unknown) => (v === undefined ? null : typeof v === "boolean" ? Number(v) : v);

class Stmt {
  constructor(private db: DatabaseSync, private sql: string, private args: unknown[] = []) {}
  bind(...args: unknown[]) { return new Stmt(this.db, this.sql, args); }
  private p() { return this.db.prepare(this.sql); }
  async first<T>() { return ((this.p().get(...(this.args.map(norm) as never[])) as T) ?? null); }
  async all<T>() { return { results: this.p().all(...(this.args.map(norm) as never[])) as T[], success: true, meta: {} }; }
  async run() {
    const r = this.p().run(...(this.args.map(norm) as never[]));
    return { success: true, results: [], meta: { changes: Number(r.changes), last_row_id: Number(r.lastInsertRowid) } };
  }
}

export function d1() {
  const db = new DatabaseSync(":memory:");
  return {
    raw: db,
    prepare: (sql: string) => new Stmt(db, sql),
    exec: async (sql: string) => { db.exec(sql); return { count: 1, duration: 0 }; },
    batch: async (stmts: Stmt[]) => {
      db.exec("BEGIN");
      try {
        const out = [];
        for (const s of stmts) out.push(await s.run());
        db.exec("COMMIT");
        return out;
      } catch (e) {
        db.exec("ROLLBACK");
        throw e;
      }
    },
  } as unknown as D1Database & { raw: DatabaseSync };
}

export function r2() {
  const store = new Map<string, { data: Uint8Array; type?: string }>();
  const obj = (key: string, data: Uint8Array) => ({ key, size: data.byteLength, body: new Response(data as BodyInit).body,
    arrayBuffer: async () => data.buffer, httpMetadata: {} });
  return {
    store,
    head: async (key: string) => (store.has(key) ? { key, size: store.get(key)!.data.byteLength } : null),
    get: async (key: string, opts?: { range?: { offset: number; length: number } }) => {
      const o = store.get(key);
      if (!o) return null;
      const d = opts?.range ? o.data.slice(opts.range.offset, opts.range.offset + opts.range.length) : o.data;
      return obj(key, d);
    },
    put: async (key: string, body: ReadableStream | Uint8Array | string, meta?: { httpMetadata?: { contentType?: string } }) => {
      const data = body instanceof ReadableStream ? new Uint8Array(await new Response(body).arrayBuffer())
        : typeof body === "string" ? new TextEncoder().encode(body) : body;
      store.set(key, { data, type: meta?.httpMetadata?.contentType });
      return obj(key, data);
    },
    delete: async (key: string) => void store.delete(key),
  } as unknown as R2Bucket & { store: Map<string, { data: Uint8Array; type?: string }> };
}

/** FixedLengthStream is a Workers runtime global; an identity TransformStream is equivalent for tests. */
export function installWorkerGlobals() {
  (globalThis as Record<string, unknown>).FixedLengthStream ??= class extends TransformStream {
    constructor(_len: number) { super(); }
  };
}
