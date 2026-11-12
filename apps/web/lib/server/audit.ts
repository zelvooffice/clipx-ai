/// <reference types="@cloudflare/workers-types" />
import { now, run, s } from "./db";

export async function audit(db: D1Database, actor: string, action: string, target: string | null = null,
                            detail: unknown = {}, ip: string | null = null): Promise<void> {
  await run(db, "INSERT INTO audit_logs (at, actor, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
    now(), actor, action, target, s(detail), ip);
}

export async function event(db: D1Database, level: "INFO" | "WARNING" | "ERROR", component: string, ev: string,
                            message: string, streamId: string | null = null): Promise<void> {
  await run(db, "INSERT INTO events (at, level, component, event, message, stream_id) VALUES (?, ?, ?, ?, ?, ?)",
    now(), level, component, ev, message, streamId);
}
