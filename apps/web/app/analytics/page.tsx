"use client";

import { BarChart3, Eye, Heart, MessageCircle, RefreshCw, Share2, Sparkles, Timer, Trophy } from "lucide-react";
import { useState } from "react";
import { HBarList, LineChart } from "@/components/charts";
import { Badge, Button, Card, CardHeader, Empty, ErrorState, Notice, PageHeader, Skeleton, Stat } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { ago, dateTime, num, pct } from "@/lib/format";

type Summary = {
  videos: number; totals: { views: number; likes: number; comments: number; shares: number }; engagement_rate: number | null;
  retention: { available: boolean; note: string };
  daily: { date: string; views: number; likes: number; shares: number; comments: number; posts: number }[];
  best: { video_id: string; title: string | null; views: number | null; likes: number | null; comments: number | null;
          shares: number | null; share_url: string | null; posted_at: number | null }[];
  last_synced: number | null;
  posting_times: { status: string; sample_size: number; min_required: number; note: string; timezone: string; method: string;
                   windows: { start_hour: number; end_hour: number; posts: number; median_views: number; score: number; eligible: boolean }[];
                   recommended?: { start_hour: number }[] };
};

export default function AnalyticsPage() {
  const { data, error, loading, reload } = useApi<Summary>("/v1/analytics/summary", { refreshMs: 60000 });
  const [syncing, setSyncing] = useState(false);
  const has = !!data && data.videos > 0;

  async function sync() {
    setSyncing(true);
    try {
      await api("/v1/analytics/sync", { method: "POST", body: {} });
      await reload();
    } finally {
      setSyncing(false);
    }
  }

  const rec = new Set((data?.posting_times.recommended ?? []).map((r) => r.start_hour));

  return (
    <>
      <PageHeader icon={BarChart3} title="Analytics"
        subtitle={<>Performance of published clips, from TikTok&apos;s official API. {data?.last_synced && `Last synced ${ago(data.last_synced)}.`}</>}
        actions={<Button icon={RefreshCw} loading={syncing} onClick={sync}>Sync now</Button>} />
      {error && <div className="mb-4"><ErrorState error={error} onRetry={reload} /></div>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Stat label="Views" value={num(data?.totals.views)} icon={Eye} loading={loading} />
        <Stat label="Likes" value={num(data?.totals.likes)} icon={Heart} loading={loading} />
        <Stat label="Shares" value={num(data?.totals.shares)} icon={Share2} loading={loading} />
        <Stat label="Comments" value={num(data?.totals.comments)} icon={MessageCircle} loading={loading} />
        <Stat label="Engagement" value={pct(data?.engagement_rate)} icon={Sparkles} loading={loading}
          sub="(likes + comments + shares) / views" />
      </div>

      {!loading && !has && (
        <Card className="mt-6"><Empty icon={BarChart3} title="No performance data yet"
          action={<Button onClick={sync} loading={syncing}>Sync from TikTok</Button>}>
          Analytics appear once clips are published and the TikTok account is connected with the video.list permission.
          Nothing here is estimated.
        </Empty></Card>
      )}

      {has && data && (
        <>
          <div className="mt-6 grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader title="Views" hint="Per day of posting (current totals)" />
              <div className="p-5">
                <LineChart title="Views by posting day" series={["Views"]} data={data.daily.map((d) => ({ label: d.date, values: [d.views] }))} />
              </div>
            </Card>
            <Card>
              <CardHeader title="Engagement" hint="Likes, shares and comments per day of posting" />
              <div className="p-5">
                <LineChart title="Likes, shares and comments by posting day" series={["Likes", "Shares", "Comments"]}
                  data={data.daily.map((d) => ({ label: d.date, values: [d.likes, d.shares, d.comments] }))} />
              </div>
            </Card>
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader title={<span className="flex items-center gap-2"><Trophy size={14} aria-hidden /> Best-performing clips</span>} />
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-left text-sm">
                  <thead className="text-xs text-muted">
                    <tr className="border-b border-line">{["#", "Video", "Posted", "Views", "Likes", "Shares", "Comments"].map((h) =>
                      <th key={h} className="px-5 py-2.5 font-medium">{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {data.best.map((b, i) => (
                      <tr key={b.video_id} className="border-b border-line last:border-0">
                        <td className="px-5 py-3 tabular-nums text-muted">{i + 1}</td>
                        <td className="max-w-[18rem] truncate px-5 py-3">
                          {b.share_url ? <a className="hover:underline" href={b.share_url} target="_blank" rel="noreferrer">{b.title || b.video_id}</a> : b.title}
                        </td>
                        <td className="px-5 py-3 text-muted">{dateTime(b.posted_at)}</td>
                        {[b.views, b.likes, b.shares, b.comments].map((v, j) => <td key={j} className="px-5 py-3 tabular-nums">{num(v)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
            <Card>
              <CardHeader title={<span className="flex items-center gap-2"><Timer size={14} aria-hidden /> Retention</span>} />
              <div className="p-5"><Notice tone="neutral" title="Not available">{data.retention.note}</Notice></div>
            </Card>
          </div>
        </>
      )}

      <Card className="mt-4">
        <CardHeader title="Best time to post" hint={data ? `${data.posting_times.method} · ${data.posting_times.timezone}` : undefined}
          actions={data && <Badge tone={data.posting_times.status === "ok" ? "good" : "neutral"}>
            {data.posting_times.status === "ok" ? "Evidence-based" : "Not enough data"}</Badge>} />
        <div className="p-5">
          {loading ? <Skeleton className="h-32 w-full" /> : data && data.posting_times.windows.length ? (
            <HBarList title="Median views by 3-hour posting block"
              rows={data.posting_times.windows.slice().sort((a, b) => a.start_hour - b.start_hour).map((w) => ({
                label: `${String(w.start_hour).padStart(2, "0")}:00–${String(w.end_hour).padStart(2, "0")}:00`,
                weight: `n=${w.posts}`, value: w.median_views,
                tag: rec.has(w.start_hour) ? <span className="ml-2 text-[10px] text-[#5fd35f]">Recommended</span> : undefined,
              }))}
              format={(v) => num(v)} note={data.posting_times.note} />
          ) : (
            <p className="text-sm text-muted">
              {data?.posting_times.note ?? "—"} CLIPX spaces posts using your cooldown until there is enough real data to recommend times.
            </p>
          )}
        </div>
      </Card>
    </>
  );
}
