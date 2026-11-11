"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : formatDetail(detail));
  }
}

function formatDetail(d: unknown): string {
  if (d && typeof d === "object" && "errors" in d && Array.isArray((d as { errors: unknown[] }).errors)) {
    return (d as { errors: string[] }).errors.join(" · ");
  }
  if (Array.isArray(d)) return d.map((x) => (x as { msg?: string }).msg ?? String(x)).join(" · ");
  return JSON.stringify(d);
}

function csrf(): string {
  const m = document.cookie.match(/(?:^|; )clipx_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export async function api<T = unknown>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = init.method ?? (init.body !== undefined ? "POST" : "GET");
  const res = await fetch(`/api/backend${path}`, {
    method,
    headers: {
      ...(init.body !== undefined ? { "content-type": "application/json" } : {}),
      ...(method !== "GET" ? { "x-clipx-csrf": csrf() } : {}),
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    credentials: "same-origin",
  });
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) throw new ApiError(res.status, (data as { detail?: unknown })?.detail ?? text ?? res.statusText);
  return data as T;
}

function safeJson(t: string): unknown {
  try {
    return JSON.parse(t);
  } catch {
    return t;
  }
}

export function useApi<T>(path: string | null, opts: { refreshMs?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const alive = useRef(true);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    setLoading(!!path);
    load();
    if (!opts.refreshMs) return () => void (alive.current = false);
    const id = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, opts.refreshMs);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [load, opts.refreshMs, path]);

  return { data, error, loading, reload: load };
}

/** Local-dev media is served by the API (signed proxy); production media URLs are short-lived R2 links. */
export function mediaUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  return url.startsWith("/v1/") ? `/api/backend${url}` : url;
}
