/**
 * Content-aware edit decisions (port of services/api/app/video/edit_plan.py): punch-ins on emphasised words and audio
 * peaks, slow push-ins for calmer moments, fades. Seeded by clip id + render version, so two clips never get an
 * identical edit and re-rendering the same version is deterministic.
 */
import type { Cue } from "./subtitles";

export interface Zoom { start: number; end: number; factor: number; ease_in: number; ease_out: number; reason: string }
export interface EditPlan { style: "snap" | "glide"; intensity: number; push_in: number; zooms: Zoom[]; fade_in: number; fade_out: number; notes: string[] }

const INTENSITY: Record<string, number> = { funny: 1, hype_gameplay: 0.9, unexpected: 0.85, reaction: 0.8, argument: 0.6,
  memorable_quote: 0.5, wholesome: 0.4, storytelling: 0.35, emotional: 0.25, moment: 0.7 };

/** Deterministic PRNG (mulberry32) from a string seed (FNV-1a). */
export function rng(seed: string) {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619);
  let a = h >>> 0;
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return { next, uniform: (lo: number, hi: number) => lo + (hi - lo) * next(), choice: <T,>(xs: T[]) => xs[Math.floor(next() * xs.length)] };
}

export function buildPlan(o: { clipId: string; category: string; duration: number; cues: Cue[]; audioPeaks: number[];
                                zoomIntensity?: number; version?: number }): EditPlan {
  const r = rng(`${o.clipId}:${o.version ?? 1}`);
  const intensity = Math.max(0, Math.min(1.2, (INTENSITY[o.category] ?? 0.6) * (o.zoomIntensity ?? 1)));
  const style = intensity >= 0.75 ? r.choice(["snap", "glide"] as const) : "glide";
  const plan: EditPlan = { style, intensity: Math.round(intensity * 100) / 100, push_in: 1, zooms: [], fade_in: 0.12, fade_out: 0.35,
    notes: [`category '${o.category}' -> intensity ${intensity.toFixed(2)}, style ${style}`] };
  if (intensity === 0) {
    plan.notes.push("punch-ins disabled");
    return plan;
  }
  if (["storytelling", "emotional", "wholesome", "memorable_quote"].includes(o.category)) {
    plan.push_in = 1 + r.uniform(0.04, 0.08) * Math.min(1, intensity * 2);
    plan.notes.push(`slow push-in to ${plan.push_in.toFixed(2)}x`);
  }
  const emph = o.cues.flatMap((c) => c.words.filter((w) => w.emph).map((w) => [w.start, "emphasised word"] as [number, string]));
  const candidates = [...emph, ...o.audioPeaks.map((t) => [t, "audio peak"] as [number, string])].sort((a, b) => a[0] - b[0]);
  const budget = Math.max(0, Math.round((o.duration / 8) * intensity));
  const minGap = intensity > 0.7 ? 3.5 : 5.5;
  const chosen: [number, string][] = [];
  for (const [t, why] of candidates) {
    if (chosen.length >= budget) break;
    if (t < 0.8 || t > o.duration - 1.2) continue;
    if (chosen.every((c) => Math.abs(t - c[0]) >= minGap)) chosen.push([t, why]);
  }
  for (const [t, why] of chosen) {
    const factor = 1 + r.uniform(0.08, 0.2) * intensity;
    const hold = r.uniform(0.7, 1.6);
    const snap = style === "snap" && r.next() < 0.6;
    plan.zooms.push({ start: round(t - 0.05), end: round(Math.min(o.duration, t + hold)), factor: round(factor), ease_in: snap ? 0 : 0.14,
      ease_out: 0.22, reason: why });
  }
  plan.notes.push(`${plan.zooms.length} punch-ins`);
  return plan;
}

const round = (x: number) => Math.round(x * 1000) / 1000;

/** ffmpeg expression (variable `it` = input time) for the zoom factor at time t — mirrors EditPlan.zoom_at. */
export function zoomExpr(plan: EditPlan, duration: number): string {
  const parts: string[] = [];
  if (plan.push_in !== 1) parts.push(`(1+${(plan.push_in - 1).toFixed(4)}*it/${Math.max(duration, 0.001).toFixed(3)})`);
  for (const z of plan.zooms) {
    const up = z.ease_in > 0 ? `(it-${z.start})/${z.ease_in}` : "1";
    const down = z.ease_out > 0 ? `(${z.end}-it)/${z.ease_out}` : "1";
    const k = `clip(min(${up},${down}),0,1)`;
    // smoothstep k*k*(3-2k), only inside [start,end]
    parts.push(`(1+${(z.factor - 1).toFixed(4)}*between(it,${z.start},${z.end})*${k}*${k}*(3-2*${k}))`);
  }
  return parts.length ? parts.join("*") : "1";
}

/** Times (s) of the loudest moments, from mono PCM. Used as punch-in candidates. */
export function audioPeaks(pcm: Float32Array, sampleRate: number, top = 6): number[] {
  const win = Math.floor(sampleRate * 0.25);
  const rms: [number, number][] = [];
  for (let i = 0; i + win <= pcm.length; i += win) {
    let s = 0;
    for (let k = i; k < i + win; k++) s += pcm[k] * pcm[k];
    rms.push([Math.sqrt(s / win), i / sampleRate]);
  }
  if (!rms.length) return [];
  const mean = rms.reduce((a, b) => a + b[0], 0) / rms.length;
  return rms.filter(([v]) => v > mean * 1.8).sort((a, b) => b[0] - a[0]).slice(0, top).map(([, t]) => round(t)).sort((a, b) => a - b);
}
