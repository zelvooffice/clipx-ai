"use client";
/**
 * Background clip processor. On the free plan video is edited inside an open dashboard tab: this provider polls the
 * queue, claims one clip at a time (the server makes claims exclusive across tabs and users) and renders it with
 * ffmpeg.wasm. A Web Lock keeps it to one processing tab per browser.
 */
import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Progress } from "@/lib/render/engine";
import type { ClipDetail, ProcessingSettings } from "@/lib/types";

type Current = { id: string; title: string; progress: Progress } | null;
interface ProcessorState {
  enabled: boolean; current: Current; waiting: number; lastError: string | null; done: number;
  setPaused: (p: boolean) => void; paused: boolean; processNow: (clipId: string) => Promise<void>;
}

const Ctx = createContext<ProcessorState>({ enabled: false, current: null, waiting: 0, lastError: null, done: 0,
  setPaused: () => undefined, paused: false, processNow: async () => undefined });
export const useProcessor = () => useContext(Ctx);

const PAUSE_KEY = "clipx_processor_paused";

export function ProcessorProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<ProcessingSettings | null>(null);
  const [current, setCurrent] = useState<Current>(null);
  const [waiting, setWaiting] = useState(0);
  const [lastError, setLastError] = useState<string | null>(null);
  const [done, setDone] = useState(0);
  const [paused, setPausedState] = useState(false);
  const busy = useRef(false);

  useEffect(() => {
    try { setPausedState(localStorage.getItem(PAUSE_KEY) === "1"); } catch { /* storage blocked */ }
    api<{ processing: ProcessingSettings }>("/v1/settings").then((s) => setSettings(s.processing)).catch(() => undefined);
  }, []);

  const setPaused = (p: boolean) => {
    setPausedState(p);
    try { localStorage.setItem(PAUSE_KEY, p ? "1" : "0"); } catch { /* storage blocked */ }
  };

  const process = useCallback(async (clip: ClipDetail, claimed: boolean): Promise<string | null> => {
    const defaults = { default_layout: settings?.default_layout ?? "fit", zoom_intensity: settings?.zoom_intensity ?? 1 };
    if (!claimed) await api(`/v1/clips/${clip.id}/claim`, { method: "POST", body: {} });
    setCurrent({ id: clip.id, title: clip.title, progress: { stage: "loading engine" } });
    try {
      const { renderClip } = await import("@/lib/render/engine");
      await renderClip(clip, defaults, (p) => setCurrent({ id: clip.id, title: clip.title, progress: p }));
      setDone((d) => d + 1);
      setLastError(null);
      return null;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setLastError(`${clip.title}: ${msg}`);
      await api(`/v1/clips/${clip.id}/release`, { method: "POST", body: { error: msg } }).catch(() => undefined);
      return msg;
    } finally {
      setCurrent(null);
    }
  }, [settings]);

  const tick = useCallback(async () => {
    if (busy.current || paused || !settings?.auto_process || document.visibilityState === "hidden") return;
    busy.current = true;
    const run = async () => {
      const queue = await api<ClipDetail[]>("/v1/queue");
      setWaiting(queue.length);
      for (const clip of queue) {
        try {
          await api(`/v1/clips/${clip.id}/claim`, { method: "POST", body: {} });
        } catch {
          continue; // another tab or user took it
        }
        await process(clip, true);
        setWaiting((w) => Math.max(0, w - 1));
        return;
      }
    };
    try {
      const locks = (navigator as Navigator & { locks?: LockManager }).locks;
      if (locks) await locks.request("clipx-render", { ifAvailable: true }, async (lock) => { if (lock) await run(); });
      else await run();
    } catch {
      /* offline or locked: try again next tick */
    } finally {
      busy.current = false;
    }
  }, [paused, settings, process]);

  useEffect(() => {
    tick();
    const id = setInterval(tick, 20000);
    return () => clearInterval(id);
  }, [tick]);

  // An unloading tab gives its claim back immediately instead of waiting for the 15 minute timeout.
  useEffect(() => {
    const onHide = () => {
      if (!current) return;
      const m = document.cookie.match(/(?:^|; )clipx_csrf=([^;]+)/);
      fetch(`/api/v1/clips/${current.id}/release`, { method: "POST", keepalive: true, credentials: "same-origin",
        headers: { "content-type": "application/json", "x-clipx-csrf": m ? decodeURIComponent(m[1]) : "",
                   "x-clipx-unlock": sessionStorage.getItem("clipx_unlock") ?? "" },
        body: JSON.stringify({ error: "the tab was closed during rendering" }) }).catch(() => undefined);
    };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, [current]);

  const processNow = useCallback(async (clipId: string) => {
    if (busy.current) throw new Error("another clip is rendering in this tab; it will be picked up next");
    busy.current = true;
    try {
      const clip = await api<ClipDetail>(`/v1/clips/${clipId}`);
      const err = await process(clip, false);
      if (err) throw new Error(`Editing failed: ${err}`);
    } finally {
      busy.current = false;
    }
  }, [process]);

  return (
    <Ctx.Provider value={{ enabled: Boolean(settings?.auto_process), current, waiting, lastError, done, paused, setPaused, processNow }}>
      {children}
    </Ctx.Provider>
  );
}
