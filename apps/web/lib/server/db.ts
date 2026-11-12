/// <reference types="@cloudflare/workers-types" />
/** Small typed helpers over D1. */
export type Row = Record<string, unknown>;

export const now = () => Math.floor(Date.now() / 1000);
export const uuid = () => crypto.randomUUID();

export async function one<T = Row>(db: D1Database, sql: string, ...args: unknown[]): Promise<T | null> {
  return (await db.prepare(sql).bind(...args).first<T>()) ?? null;
}

export async function all<T = Row>(db: D1Database, sql: string, ...args: unknown[]): Promise<T[]> {
  const r = await db.prepare(sql).bind(...args).all<T>();
  return r.results ?? [];
}

export async function run(db: D1Database, sql: string, ...args: unknown[]): Promise<number> {
  const r = await db.prepare(sql).bind(...args).run();
  return r.meta?.changes ?? 0;
}

export function j<T>(v: unknown, fallback: T): T {
  if (v === null || v === undefined || v === "") return fallback;
  if (typeof v !== "string") return v as T;
  try {
    return JSON.parse(v) as T;
  } catch {
    return fallback;
  }
}

export const s = (v: unknown) => JSON.stringify(v ?? null);
