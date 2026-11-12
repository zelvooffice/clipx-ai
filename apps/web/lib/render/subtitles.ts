/**
 * Subtitle cues + ASS rendering (TypeScript port of services/api/app/video/subtitles.py).
 * Style: bold Inter ExtraBold, bright yellow (#FFE11A), heavy dark outline + soft shadow, short 1-line cues (2-4 words)
 * that follow speech from word timestamps; a few important words are emphasised (white + quick scale pop).
 */
export const OUT_W = 1080;
export const OUT_H = 1920;

export interface Word { w: string; start: number; end: number; emph?: boolean }
export interface Cue { start: number; end: number; words: Word[]; zone: Zone }
export type Zone = "lower" | "middle" | "upper";

export interface SubStyle {
  font: string; size: number; primary: string; highlight: string; outline: number; shadow: number;
  max_chars: number; max_words: number; uppercase: boolean; emphasis: boolean;
}

export const DEFAULT_STYLE: SubStyle = {
  font: "Inter ExtraBold", size: 90, primary: "#FFE11A", highlight: "#FFFFFF", outline: 7, shadow: 3,
  max_chars: 20, max_words: 4, uppercase: false, emphasis: true,
};

/** Vertical centre (px) of each safe zone, clear of TikTok's bottom caption bar and right-hand buttons. */
export const ZONES: Record<Zone, number> = { lower: 1360, middle: 1080, upper: 520 };

const STOP = new Set(("the a an and or but so to of in on at for with is it it's its i i'm you he she we they me my " +
  "your this that was were be been are am do did not just like yeah uh um oh okay ok what").split(" "));

export function pickEmphasis(words: Word[], keywords: string[] = [], maxRatio = 0.12): Set<number> {
  const kw = new Set(keywords.map((k) => k.toLowerCase().replace(/^[.,!?]+|[.,!?]+$/g, "")));
  const scored: [number, number][] = [];
  words.forEach((w, i) => {
    const raw = w.w.trim();
    const bare = raw.toLowerCase().replace(/^[.,!?"']+|[.,!?"']+$/g, "");
    if (!bare || STOP.has(bare)) return;
    let s = 0;
    if (kw.has(bare)) s += 3;
    if (raw.endsWith("!")) s += 1.5;
    if (/^\d+[%$k]?$/.test(bare)) s += 1.2;
    if (raw === raw.toUpperCase() && /[A-Z]/.test(raw) && raw.length > 2) s += 1;
    if (bare.length >= 7) s += 0.4;
    if (s > 0) scored.push([s, i]);
  });
  const budget = Math.max(1, Math.floor(words.length * maxRatio));
  const chosen = new Set<number>();
  for (const [, i] of scored.sort((a, b) => b[0] - a[0] || b[1] - a[1])) {
    if (chosen.size >= budget) break;
    if ([...chosen].some((j) => Math.abs(i - j) < 4)) continue; // never two emphasised words close together
    chosen.add(i);
  }
  return chosen;
}

/** Group word timestamps (seconds, relative to the clip) into short readable cues. */
export function buildCues(words: Word[], opts: { style?: Partial<SubStyle>; keywords?: string[]; clipStart?: number; clipEnd?: number | null } = {}): Cue[] {
  const st = { ...DEFAULT_STYLE, ...opts.style };
  const clipStart = opts.clipStart ?? 0;
  const clipEnd = opts.clipEnd ?? null;
  const ws = words.filter((w) => w.w.trim() && w.end > clipStart && (clipEnd === null || w.start < clipEnd))
    .map((w) => ({ ...w, w: w.w.trim() }));
  const emph = st.emphasis ? pickEmphasis(ws, opts.keywords) : new Set<number>();
  ws.forEach((w, i) => (w.emph = emph.has(i)));
  const cues: Cue[] = [];
  let cur: Word[] = [];
  const flush = () => {
    if (cur.length) cues.push({ start: cur[0].start, end: cur[cur.length - 1].end, words: cur, zone: "lower" });
    cur = [];
  };
  for (const w of ws) {
    if (cur.length) {
      const gap = w.start - cur[cur.length - 1].end;
      const len = [...cur, w].map((x) => x.w).join(" ").length;
      if (gap > 0.6 || len > st.max_chars || cur.length >= st.max_words) flush();
    }
    cur.push(w);
    if (/[.?!,;:]$/.test(w.w) && (cur.length >= 2 || /[.?!]$/.test(w.w))) flush();
  }
  flush();
  // Timing: never overlap, a minimum on-screen time for readability, bridge small gaps.
  cues.forEach((c, i) => {
    const nxt = i + 1 < cues.length ? cues[i + 1].start : null;
    c.end = Math.max(c.end, c.start + 0.45);
    if (nxt !== null) {
      if (nxt - c.end < 0.25) c.end = nxt;
      c.end = Math.min(c.end, nxt);
    }
    if (clipEnd !== null) c.end = Math.min(c.end, clipEnd);
  });
  return cues.filter((c) => c.end > c.start);
}

/** Shift cues so they are relative to a trimmed start, dropping what falls outside. */
export function shiftCues(cues: Cue[], offset: number, duration: number): Cue[] {
  return cues.map((c) => ({ ...c, start: c.start - offset, end: Math.min(duration, c.end - offset),
    words: c.words.map((w) => ({ ...w, start: w.start - offset, end: w.end - offset })) }))
    .filter((c) => c.end > 0.05 && c.start < duration)
    .map((c) => ({ ...c, start: Math.max(0, c.start) }));
}

export function assColor(hex: string, alpha = 0): string {
  const h = hex.replace("#", "");
  return `&H${alpha.toString(16).padStart(2, "0")}${h.slice(4, 6)}${h.slice(2, 4)}${h.slice(0, 2)}`.toUpperCase();
}

export function assTime(t: number): string {
  t = Math.max(0, t);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t - h * 3600 - m * 60;
  return `${h}:${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
}

const esc = (t: string) => t.replace(/\\/g, "\\\\").replace(/\{/g, "(").replace(/\}/g, ")").replace(/\n/g, " ");

export function toAss(cues: Cue[], style: Partial<SubStyle> = {}): string {
  const st = { ...DEFAULT_STYLE, ...style };
  const prim = assColor(st.primary), hi = assColor(st.highlight);
  const header = `[Script Info]
ScriptType: v4.00+
PlayResX: ${OUT_W}
PlayResY: ${OUT_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,${st.font},${st.size},${prim},${prim},&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,${st.outline},${st.shadow},5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
`;
  const lines = cues.map((c) => {
    const y = ZONES[c.zone] ?? ZONES.lower;
    const body = c.words.map((w) => {
      const txt = esc(st.uppercase ? w.w.toUpperCase() : w.w);
      if (!w.emph) return txt;
      const on = Math.round(Math.max(0, w.start - c.start) * 1000);
      return `{\\c${hi}\\fscx100\\fscy100\\t(${on},${on + 90},\\fscx116\\fscy116)\\t(${on + 90},${on + 200},\\fscx108\\fscy108)}${txt}{\\c${prim}\\fscx100\\fscy100}`;
    }).join(" ");
    const pop = "\\fad(40,0)\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)";
    return `Dialogue: 0,${assTime(c.start)},${assTime(c.end)},Cap,,0,0,0,,{\\an5\\pos(${OUT_W / 2},${y})${pop}}${body}`;
  });
  return header + lines.join("\n") + "\n";
}

/** Overlay-only ASS for viral templates (banner text, numbering). */
export function bannerAss(items: { text: string; start: number; end: number; y: number; size?: number; color?: string }[]): string {
  const base = toAss([]);
  const lines = items.map((b) => `Dialogue: 1,${assTime(b.start)},${assTime(b.end)},Cap,,0,0,0,,{\\an5\\pos(${OUT_W / 2},${b.y})` +
    `\\fs${b.size ?? 76}\\c${assColor(b.color ?? "#FFFFFF")}\\fad(120,120)}${esc(b.text)}`);
  return base + lines.join("\n") + "\n";
}
