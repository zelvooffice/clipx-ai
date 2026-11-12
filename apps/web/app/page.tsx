"use client";

import { Activity, CircleAlert, Film, LayoutDashboard, Radio, Send, Timer } from "lucide-react";
import Link from "next/link";
import { GroupedBars } from "@/components/charts";
import { Badge, Button, Card, CardHeader, Empty, ErrorState, Notice, PageHeader, Skeleton, Stat, linkButton } from "@/components/ui";
import { useApi } from "@/lib/api";
import { ago, dateTime, label } from "@/lib/format";
import { useProcessor } from "@/components/Processor";
import type { Activity as Act, Capabilities, Stream } from "@/lib/types";

type Overview = {
  live: Stream | null; last_session: Stream | null;
  counts: { clips: number; rendered: number; to_process: number; published: number; awaiting_confirmation: number };
  clips_per_day: { date: string; clips: number }[]; published_per_day: { date: string; published: number }[];
  activity: Act[]; capabilities: Capabilities;
};

function last14(o: Overview) {
  const days: { label: string; values: number[] }[] = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
    days.push({
      label: d,
      values: [o.clips_per_day.find((x) => x.date === d)?.clips ?? 0, o.published_per_day.find((x) => x.date === d)?.published ?? 0],
    });
  }
  return days;
}

export default function OverviewPage() {
  const { data, error, loading, reload } = useApi<Overview>("/v1/overview", { refreshMs: 20000 });
  const s = data?.live ?? data?.last_session ?? null;
  const proc = useProcessor();
  const processing = proc.current ? `Editing 1 clip` : data ? (data.counts.to_process ? `${data.counts.to_process} to edit` : "Idle") : "";

  return (
    <>
      <PageHeader icon={LayoutDashboard} title="Overview" subtitle="Everything CLIPX is doing for Lacy's channel, at a glance." />
      {error && <div className="mb-6"><ErrorState error={error} onRetry={reload} /></div>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="p-5 sm:col-span-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wider text-muted">Stream</span>
            {data?.live ? <Badge tone="critical" icon={Radio}>Live</Badge> : data && <Badge tone="neutral">Offline</Badge>}
          </div>
          {loading ? <Skeleton className="mt-4 h-7 w-3/4" /> : (
            <>
              <p className="mt-3 truncate text-xl font-semibold tracking-tight">
                {s?.title ?? "No stream recorded yet"}
              </p>
              <p className="mt-1.5 text-xs text-muted">
                {data?.live
                  ? `Live since ${dateTime(data.live.started_at)}${data.live.game ? ` · ${data.live.game}` : ""}`
                  : s ? `Last stream ended ${ago(s.ended_at)}` : "CLIPX starts working automatically when Lacy goes live."}
              </p>
            </>
          )}
        </Card>
        <Stat label="Processing" value={<span className="text-xl">{processing || "—"}</span>} icon={Timer} loading={loading}
          sub={proc.current ? `"${proc.current.title}" in this tab` : "Clips are edited in an open dashboard tab"} />
        <Stat label="Awaiting confirmation" value={data?.counts.awaiting_confirmation ?? 0} icon={Send} loading={loading}
          sub={<Link className="hover:text-fg-2" href="/publishing">Review in Publishing →</Link>} />
        <Stat label="Clips generated" value={data?.counts.clips ?? 0} icon={Film} loading={loading}
          sub={`${data?.counts.rendered ?? 0} rendered and ready`} />
        <Stat label="Clips published" value={data?.counts.published ?? 0} icon={Send} loading={loading}
          sub="Posted or delivered to the TikTok inbox" />
        <Stat label="This stream" value={s?.stats?.selected ?? 0} icon={Activity} loading={loading}
          sub={s ? `selected from ${s.stats?.viewer_clips ?? 0} viewer clips · ${s.stats?.duplicates ?? 0} duplicates skipped` : "No stream yet"} />
        <Stat label="How moments are found" value={<span className="text-xl">Viewer clips</span>}
          icon={Radio} loading={loading} sub="Ranked by Twitch views — no real-time A/V analysis" />
      </div>

      {data && data.capabilities.notes.length > 0 && (
        <div className="mt-6">
          <Notice tone="warn" title="Some capabilities are not available yet"
            action={<Link href="/settings" className={linkButton}>Open settings</Link>}>
            <ul className="list-disc space-y-0.5 pl-4">{data.capabilities.notes.map((n) => <li key={n}>{n}</li>)}</ul>
          </Notice>
        </div>
      )}

      <div className="mt-6 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Clips generated vs. published" hint="Last 14 days" />
          <div className="p-5">
            {loading ? <Skeleton className="h-52 w-full" /> : data && (data.counts.clips > 0 || data.counts.published > 0) ? (
              <GroupedBars title="Clips generated and published per day" data={last14(data)} series={["Generated", "Published"]} />
            ) : (
              <Empty icon={Film} title="No clips yet">Clips appear here once viewers clip moments on Lacy's stream and they are edited.</Empty>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Recent activity" />
          <ul className="divide-y divide-line">
            {loading && [0, 1, 2, 3].map((i) => <li key={i} className="px-5 py-3"><Skeleton className="h-4 w-full" /></li>)}
            {data?.activity.length === 0 && <li><Empty icon={CircleAlert} title="No activity yet" /></li>}
            {data?.activity.map((a, i) => (
              <li key={i} className="px-5 py-3">
                <p className="text-sm text-fg-2">{a.message}</p>
                <p className="mt-0.5 text-xs text-muted">{label(a.component)} · {ago(a.at)}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
