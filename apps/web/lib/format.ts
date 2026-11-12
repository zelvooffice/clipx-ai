export const nf = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

export function num(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : nf.format(n);
}

export function pct(n: number | null | undefined, digits = 1): string {
  return n === null || n === undefined ? "—" : `${(n * 100).toFixed(digits)}%`;
}

export function score(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : Math.round(n * 100).toString();
}

export type When = string | number | null | undefined;

/** Accepts ISO strings or epoch seconds (the API stores Unix time). */
export function toDate(v: When): Date | null {
  if (v === null || v === undefined || v === "") return null;
  return new Date(typeof v === "number" ? (v < 1e12 ? v * 1000 : v) : v);
}

export function ago(v: When): string {
  const d = toDate(v);
  if (!d) return "—";
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 0) return `in ${Math.ceil(-s / 60)}m`;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function dateTime(v: When): string {
  const d = toDate(v);
  if (!d) return "—";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function clock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

const WORDS: Record<string, string> = {
  Llm: "LLM", Tiktok: "TikTok", Eventsub: "EventSub", Uri: "URI", Url: "URL", Jwt: "JWT", Api: "API", Vod: "VOD",
  Av: "A/V", Youtube: "YouTube",
};

export function label(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\b[A-Z][a-z]+\b/g, (w) => WORDS[w] ?? w);
}
