/** Short-form captions + hashtags. Always #Lacy, 3-6 relevant tags, no spam tags, never reused. */
const CATEGORY_TAGS: Record<string, string[]> = {
  funny: ["#funny", "#streamerclips"], reaction: ["#reaction", "#streamerclips"], moment: ["#streamerclips"],
  hype_gameplay: ["#gaming", "#clutch"], storytelling: ["#storytime"], unexpected: ["#unexpected"],
};
const HOOKS: Record<string, string[]> = {
  moment: ["Chat clipped this Lacy moment for a reason 👀", "Lacy's viewers had to clip this 😭", "This Lacy moment went straight to the clips 🔥",
           "Lacy really did this live 😳", "The clip chat couldn't stop replaying 🔁"],
  funny: ["Lacy really said this with a straight face 😭", "I can't stop replaying this Lacy moment 😂", "Lacy was NOT ready for this 💀"],
  reaction: ["Lacy's reaction says everything 😳", "Watch Lacy's face at the end 👀"],
};
const SPAM = new Set(["#fyp", "#foryou", "#viral", "#foryoupage"]);

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

function shingles(t: string): Set<string> {
  const w = t.toLowerCase().match(/[a-z0-9']+/g) ?? [];
  const out = new Set<string>();
  for (let i = 0; i + 3 <= w.length; i++) out.add(w.slice(i, i + 3).join(" "));
  return out;
}

export function similarity(a: string, b: string): number {
  const x = shingles(a), y = shingles(b);
  if (!x.size || !y.size) return a.trim().toLowerCase() === b.trim().toLowerCase() ? 1 : 0;
  let inter = 0;
  for (const v of x) if (y.has(v)) inter++;
  return inter / (x.size + y.size - inter);
}

export function normalizeTags(tags: string[], game?: string | null): string[] {
  const out: string[] = [];
  for (const t of tags) {
    const tag = "#" + t.replace(/^#/, "").replace(/[^A-Za-z0-9_]/g, "");
    if (tag.length > 1 && !SPAM.has(tag.toLowerCase()) && !out.some((o) => o.toLowerCase() === tag.toLowerCase())) out.push(tag);
  }
  const rest = out.filter((t) => t.toLowerCase() !== "#lacy");
  const res = ["#Lacy", ...rest];
  if (game) {
    const g = "#" + game.replace(/[^A-Za-z0-9]/g, "");
    if (g.length > 2 && g.length <= 25 && !res.some((x) => x.toLowerCase() === g.toLowerCase()) && res.length < 6) res.push(g);
  }
  return res.slice(0, 6);
}

function bestQuote(transcript: string): string | null {
  const sents = transcript.split(/(?<=[.?!])\s+/).map((x) => x.trim()).filter((x) => x.length >= 12 && x.length <= 70);
  if (!sents.length) return null;
  return sents.sort((a, b) => (b.match(/[!?]/g)?.length ?? 0) - (a.match(/[!?]/g)?.length ?? 0) || Math.abs(a.length - 40) - Math.abs(b.length - 40))[0];
}

export function templateCaption(o: { clipId: string; title: string; category: string; transcript: string; game?: string | null;
                                     recent: string[] }): { caption: string; hashtags: string[]; source: "template" } {
  const hooks = [...(HOOKS[o.category] ?? HOOKS.moment)];
  const seed = hash(o.clipId);
  hooks.sort((a, b) => (hash(a + seed) % 997) - (hash(b + seed) % 997));
  const quote = bestQuote(o.transcript);
  let caption = hooks[0];
  for (const h of hooks) {
    const cand = quote && seed % 5 < 3 ? `${h} "${quote}"` : h;
    if (!o.recent.some((r) => r.toLowerCase() === cand.toLowerCase() || similarity(r, cand) > 0.5)) { caption = cand; break; }
    caption = `${hooks[0]} — ${o.title.slice(0, 60)}`;
  }
  return { caption, hashtags: normalizeTags(["#Lacy", "#twitch", "#twitchclips", ...(CATEGORY_TAGS[o.category] ?? [])], o.game), source: "template" };
}
