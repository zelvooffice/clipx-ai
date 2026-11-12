"use client";
/**
 * In-browser renderer (Cloudflare free plan: no server-side video processing). ffmpeg.wasm (single-thread core, served
 * gz-compressed from /ffmpeg) renders 1080x1920 H.264; Web Audio extracts 16 kHz mono audio for Workers AI Whisper.
 * Only media fetched from our own R2 (authorized sources) is processed.
 */
import type { FFmpeg } from "@ffmpeg/ffmpeg";
import { api } from "../api";
import { clipArgs, compilationArgs, povArgs, stackArgs } from "./filters";
import { audioPeaks, buildPlan } from "./plan";
import { bannerAss, buildCues, type Cue, shiftCues, type SubStyle, toAss, type Word } from "./subtitles";

export type Stage = "loading engine" | "downloading" | "analysing audio" | "transcribing" | "rendering" | "uploading" | "done";
export interface Progress { stage: Stage; ratio?: number }
type OnProgress = (p: Progress) => void;

let engine: Promise<FFmpeg> | null = null;
let font: Uint8Array | null = null;

async function load(): Promise<FFmpeg> {
  const { FFmpeg } = await import("@ffmpeg/ffmpeg");
  const ff = new FFmpeg();
  const res = await fetch("/ffmpeg/ffmpeg-core.wasm.gz");
  if (!res.ok) throw new Error(`could not download the video engine (${res.status})`);
  const raw = await res.arrayBuffer();
  const head = new Uint8Array(raw, 0, 2);
  // gzip magic 1f 8b; if a proxy already decoded it, use the bytes as they are
  const wasm = head[0] === 0x1f && head[1] === 0x8b
    ? await new Response(new Blob([raw]).stream().pipeThrough(new DecompressionStream("gzip"))).blob()
    : new Blob([raw]);
  const wasmURL = URL.createObjectURL(new Blob([wasm], { type: "application/wasm" }));
  await ff.load({ coreURL: `${location.origin}/ffmpeg/ffmpeg-core.js`, wasmURL });
  font ??= new Uint8Array(await (await fetch("/fonts/Inter-ExtraBold.otf")).arrayBuffer());
  await ff.createDir("/fonts").catch(() => undefined);
  await ff.writeFile("/fonts/Inter-ExtraBold.otf", font);
  return ff;
}

/** One engine per tab; it is rebuilt after every job so wasm memory never accumulates. */
async function withEngine<T>(fn: (ff: FFmpeg) => Promise<T>): Promise<T> {
  engine ??= load();
  let ff: FFmpeg;
  try {
    ff = await engine;
  } catch (e) {
    engine = null;
    throw e;
  }
  try {
    return await fn(ff);
  } finally {
    try { ff.terminate(); } catch { /* already gone */ }
    engine = null;
  }
}

async function exec(ff: FFmpeg, args: string[], duration: number, onProgress?: OnProgress): Promise<Uint8Array> {
  const logs: string[] = [];
  const onLog = ({ message }: { message: string }) => { logs.push(message); if (logs.length > 40) logs.shift(); };
  const onProg = ({ time }: { time: number }) => onProgress?.({ stage: "rendering", ratio: Math.min(1, time / 1e6 / Math.max(duration, 0.1)) });
  ff.on("log", onLog);
  ff.on("progress", onProg);
  try {
    const rc = await ff.exec(args);
    if (rc !== 0) throw new Error(`ffmpeg failed (${rc}): ${logs.slice(-4).join(" | ")}`.slice(0, 380));
    const out = await ff.readFile("out.mp4");
    if (typeof out === "string" || out.byteLength < 1000) throw new Error("ffmpeg produced no video");
    return out;
  } finally {
    ff.off("log", onLog);
    ff.off("progress", onProg);
  }
}

// ------------------------------------------------------------------------------------- media helpers
export async function probe(blob: Blob): Promise<{ width: number; height: number; duration: number }> {
  const url = URL.createObjectURL(blob);
  try {
    const v = document.createElement("video");
    v.muted = true;
    v.preload = "metadata";
    v.src = url;
    await new Promise<void>((ok, fail) => { v.onloadedmetadata = () => ok(); v.onerror = () => fail(new Error("the browser cannot read this video")); });
    return { width: v.videoWidth, height: v.videoHeight, duration: v.duration };
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** JPEG poster frame drawn from the rendered video (no extra ffmpeg run). */
export async function thumbnail(blob: Blob, at = 1.5): Promise<Blob> {
  const url = URL.createObjectURL(blob);
  try {
    const v = document.createElement("video");
    v.muted = true;
    v.src = url;
    await new Promise<void>((ok, fail) => { v.onloadeddata = () => ok(); v.onerror = () => fail(new Error("thumbnail failed")); });
    v.currentTime = Math.min(at, Math.max(0, v.duration / 3));
    await new Promise<void>((ok) => (v.onseeked = () => ok()));
    const c = document.createElement("canvas");
    c.width = 540;
    c.height = 960;
    c.getContext("2d")!.drawImage(v, 0, 0, c.width, c.height);
    return await new Promise<Blob>((ok, fail) => c.toBlob((b) => (b ? ok(b) : fail(new Error("thumbnail failed"))), "image/jpeg", 0.85));
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Decode the clip's audio to 16 kHz mono (Whisper input) with Web Audio. Returns null when there is no audio track. */
export async function decodeAudio(buf: ArrayBuffer): Promise<Float32Array | null> {
  try {
    const ctx = new OfflineAudioContext(1, 1, 16000);
    const ab = await ctx.decodeAudioData(buf.slice(0));
    const out = new Float32Array(ab.length);
    for (let ch = 0; ch < ab.numberOfChannels; ch++) {
      const d = ab.getChannelData(ch);
      for (let i = 0; i < d.length; i++) out[i] += d[i] / ab.numberOfChannels;
    }
    return out;
  } catch {
    return null;
  }
}

export function wav16(pcm: Float32Array, rate = 16000): Uint8Array {
  const b = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(b);
  const str = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 0x7fff, true);
  return new Uint8Array(b);
}

async function download(url: string): Promise<ArrayBuffer> {
  const r = await fetch(url, { credentials: "same-origin" });
  if (!r.ok) throw new Error(`could not download ${url.split("?")[0]} (${r.status})`);
  return r.arrayBuffer();
}

// ------------------------------------------------------------------------------------- clip render
export interface ClipDetail {
  id: string; title: string; category: string; render_version: number; source_url: string | null;
  words: Word[]; cues: Cue[]; cues_edited: boolean; edit: Record<string, unknown>;
}

export interface ProcessingDefaults { default_layout: "fit" | "crop"; zoom_intensity: number }

/** Full pipeline for one clip. The caller must have claimed it (POST /clips/:id/claim). */
export async function renderClip(clip: ClipDetail, defaults: ProcessingDefaults, onProgress: OnProgress = () => undefined): Promise<void> {
  if (!clip.source_url) throw new Error("clip has no source media");
  onProgress({ stage: "downloading" });
  const src = await download(clip.source_url);
  const blob = new Blob([src], { type: "video/mp4" });
  const info = await probe(blob);
  onProgress({ stage: "analysing audio" });
  const pcm = await decodeAudio(src);
  let words = clip.words ?? [];
  if (!words.length && pcm && pcm.length > 16000 * 0.5) {
    onProgress({ stage: "transcribing" });
    const t = await api<{ words: Word[] }>(`/v1/clips/${clip.id}/transcribe`, { method: "POST", raw: wav16(pcm), contentType: "audio/wav" });
    words = t.words;
  }
  const e = clip.edit ?? {};
  const trimStart = Math.max(0, Number(e.trim_start ?? 0));
  const trimEnd = Math.min(info.duration, Number(e.trim_end ?? info.duration) || info.duration);
  const duration = Math.max(1, trimEnd - trimStart);
  const style: Partial<SubStyle> = {
    ...(e.sub_size ? { size: Number(e.sub_size) } : {}), ...(e.sub_color ? { primary: String(e.sub_color) } : {}),
    ...(e.sub_highlight ? { highlight: String(e.sub_highlight) } : {}), ...(typeof e.sub_uppercase === "boolean" ? { uppercase: e.sub_uppercase } : {}),
  };
  const cues = shiftCues(clip.cues_edited && clip.cues?.length ? clip.cues : buildCues(words), trimStart, duration);
  const peaks = pcm ? audioPeaks(pcm, 16000).map((t) => t - trimStart).filter((t) => t > 0 && t < duration) : [];
  const plan = buildPlan({ clipId: clip.id, category: clip.category, duration, cues, audioPeaks: peaks,
    zoomIntensity: Number(e.zoom_intensity ?? defaults.zoom_intensity), version: clip.render_version + 1 });
  const out = await withEngine(async (ff) => {
    onProgress({ stage: "rendering", ratio: 0 });
    await ff.writeFile("in.mp4", new Uint8Array(src));
    if (cues.length) await ff.writeFile("subs.ass", new TextEncoder().encode(toAss(cues, style)));
    return exec(ff, clipArgs({ srcW: info.width, srcH: info.height, duration, trimStart, plan, hasAudio: Boolean(pcm),
      subtitles: cues.length > 0, layout: (e.layout as "fit" | "crop") ?? defaults.default_layout, cropX: (e.crop_x as number | null) ?? null }),
      duration, onProgress);
  });
  await upload(`/v1/clips/${clip.id}`, out, duration, onProgress);
}

async function upload(base: string, out: Uint8Array, duration: number, onProgress: OnProgress) {
  onProgress({ stage: "uploading" });
  const video = new Blob([out as BlobPart], { type: "video/mp4" });
  const thumb = await thumbnail(video).catch(() => null);
  if (thumb && base.includes("/clips/")) await api(`${base}/thumb`, { method: "PUT", raw: thumb, contentType: "image/jpeg" });
  await api(`${base}/render?duration=${duration.toFixed(2)}`, { method: "PUT", raw: video, contentType: "video/mp4" });
  onProgress({ stage: "done", ratio: 1 });
}

// ------------------------------------------------------------------------------------- viral templates
export interface ViralInput { id: string; video_url: string; duration_s: number | null }

export async function renderViral(o: { editId: string; template: string; text: string; clips: ViralInput[] }, onProgress: OnProgress = () => undefined) {
  onProgress({ stage: "downloading" });
  const bufs = await Promise.all(o.clips.map((c) => download(c.video_url)));
  const durs = await Promise.all(bufs.map(async (b) => (await probe(new Blob([b], { type: "video/mp4" }))).duration));
  let args: string[], duration: number, banner: string;
  if (o.template === "pov") {
    duration = durs[0];
    banner = bannerAss([{ text: `POV: ${o.text || "you're in Lacy's chat"}`, start: 0, end: duration, y: 300, size: 72 }]);
    args = povArgs(duration);
  } else if (o.template === "compilation") {
    const each = Math.min(18, Math.max(6, 90 / durs.length));
    let t = 0;
    const items = durs.map((d, i) => {
      const len = Math.min(d, each);
      const it = { text: `#${durs.length - i}`, start: t, end: t + 1.6, y: 300, size: 120, color: "#FFE11A" };
      t += len;
      return it;
    });
    duration = t;
    banner = bannerAss(o.text ? [{ text: o.text, start: 0, end: duration, y: 170, size: 64 }, ...items] : items);
    args = compilationArgs(durs, each);
  } else if (o.template === "how_me_and_bro_look") {
    duration = Math.min(durs[0], durs[1]);
    banner = bannerAss([{ text: o.text || "how me and bro look", start: 0, end: duration, y: 960, size: 70 }]);
    args = stackArgs(duration);
  } else {
    throw new Error(`unknown template ${o.template}`);
  }
  const out = await withEngine(async (ff) => {
    onProgress({ stage: "rendering", ratio: 0 });
    for (let i = 0; i < bufs.length; i++) await ff.writeFile(`in${i}.mp4`, new Uint8Array(bufs[i]));
    await ff.writeFile("banner.ass", new TextEncoder().encode(banner));
    return exec(ff, args, duration, onProgress);
  });
  onProgress({ stage: "uploading" });
  const video = new Blob([out as BlobPart], { type: "video/mp4" });
  const r = await api<{ clip_id: string }>(`/v1/viral/edits/${o.editId}/render?duration=${duration.toFixed(2)}`, { method: "PUT", raw: video, contentType: "video/mp4" });
  const thumb = await thumbnail(video).catch(() => null);
  if (thumb) await api(`/v1/clips/${r.clip_id}/thumb`, { method: "PUT", raw: thumb, contentType: "image/jpeg" });
  onProgress({ stage: "done", ratio: 1 });
}
