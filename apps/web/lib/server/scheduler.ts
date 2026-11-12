/// <reference types="@cloudflare/workers-types" />
/** Evidence-based posting times: 3-hour blocks, empirical-Bayes shrunk mean log-views; >= 10 measured posts. */
import { all } from "./db";

const MIN_POSTS = 10, MIN_PER_BLOCK = 3, PRIOR = 4, BLOCK_H = 3;

function hourIn(tz: string, t: number): number {
  try {
    return Number(new Intl.DateTimeFormat("en-GB", { hour: "numeric", hour12: false, timeZone: tz }).format(new Date(t * 1000))) % 24;
  } catch {
    return new Date(t * 1000).getUTCHours();
  }
}

export async function recommendations(db: D1Database, tz: string) {
  const rows = await all<{ views: number | null; likes: number | null; comments: number | null; shares: number | null; posted_at: number | null }>(db,
    `SELECT a.views, a.likes, a.comments, a.shares, a.posted_at FROM analytics a
     JOIN (SELECT video_id, MAX(captured_at) m FROM analytics GROUP BY video_id) l ON l.video_id = a.video_id AND l.m = a.captured_at`);
  const now = Date.now() / 1000;
  const measured = rows.filter((r) => r.views !== null && r.posted_at && now - r.posted_at > 48 * 3600);
  const base = { timezone: tz, sample_size: measured.length, min_required: MIN_POSTS,
                 method: "3-hour blocks, empirical-Bayes shrunk mean of log(views); posts older than 48 h" };
  if (measured.length < MIN_POSTS) {
    return { ...base, status: "insufficient_data", windows: [], recommended: [],
             note: `Only ${measured.length} measured posts (need ${MIN_POSTS}). No posting-time claim is made yet.` };
  }
  const lv = (v: number | null) => Math.log1p(v ?? 0);
  const g = measured.reduce((a, r) => a + lv(r.views), 0) / measured.length;
  const blocks = new Map<number, typeof measured>();
  for (const r of measured) {
    const b = Math.floor(hourIn(tz, r.posted_at!) / BLOCK_H);
    blocks.set(b, [...(blocks.get(b) ?? []), r]);
  }
  const windows = [...blocks.entries()].map(([b, items]) => {
    const n = items.length;
    const mean = items.reduce((a, r) => a + lv(r.views), 0) / n;
    const views = items.map((i) => i.views ?? 0).sort((x, y) => x - y);
    return { start_hour: b * BLOCK_H, end_hour: b * BLOCK_H + BLOCK_H, posts: n, median_views: views[Math.floor(n / 2)],
             score: Math.round(((n * mean + PRIOR * g) / (n + PRIOR) - g) * 1000) / 1000, eligible: n >= MIN_PER_BLOCK };
  }).sort((a, b) => b.score - a.score);
  const recommended = windows.filter((w) => w.eligible && w.score > 0).slice(0, 3);
  return { ...base, status: recommended.length ? "ok" : "no_clear_winner", windows, recommended,
           note: recommended.length ? "Blocks ranked by shrunk log-view lift vs. the account average."
                                    : "No time block outperforms the account average with enough posts yet." };
}
