"use client";

import { Activity, Eye, Radio, RefreshCw, Timer, Users } from "lucide-react";
import { useMemo, useState } from "react";
import { HighlightTimeline } from "@/components/charts";
import { STATUS } from "@/components/ClipCard";
import { Badge, Button, Card, CardHeader, Empty, ErrorState, Notice, PageHeader, Skeleton, type Tone } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { ago, clock, dateTime, num } from "@/lib/format";
import type { Activity as Act, Capabilities, Clip, Stream } from "@/lib/types";

type Status = { live: Stream | null; target: string; cron: { last_run: number | null; alive: boolean }; capabilities: Capabilities };
type Timeline = { stream: Stream | null; clips: (Clip & { offset_s: number })[]; events: Act[]; streams: Stream[] };

/** Why a moment was or wasn't selected, in plain words. */
function reason(c: Clip, minViews: number | null): string {
  switch (c.status) {
    case "duplicate": return "Overlaps a more-viewed clip of the same moment, so it was skipped as a duplicate.";
    case "discovered": return (c.view_count ?? 0) < (minViews ?? 2)
      ? `Only ${c.view_count ?? 0} views — below the threshold for a highlight.`
      : "Below the per-stream maximum: higher-viewed moments were selected instead.";
    case "rejected": return c.status_detail ?? "Rejected manually.";
    default: return `Selected: one of the most viewed moments of the stream (${num(c.view_count)} views).`;
  }
}

export default function LivePage() {
  const status = useApi<Status>("/v1/live/status", { refreshMs: 10000 });
  const [streamId, setStreamId] = useState<string | null>(null);
  const tl = useApi<Timeline>(`/v1/live/timeline${streamId ? `?stream=${streamId}` : ""}`, { refreshMs: 15000 });
  const [selected, setSelected] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);
  const st = tl.data?.stream ?? status.data?.live ?? null;
  const live = st?.status === "live";
  const points = useMemo(() => (tl.data?.clips ?? []).map((c) => ({
    id: c.id, t: Math.max(0, c.offset_s), score: c.score ?? 0, category: "moment", label: c.title,
    status: c.selected || ["needs_media", "media_ready", "processing", "rendered", "failed"].includes(c.status) ? "selected" : c.status,
  })), [tl.data]);
  const sel = tl.data?.clips.find((c) => c.id === selected) ?? tl.data?.clips[0] ?? null;
  const counts = useMemo(() => {
    const c = tl.data?.clips ?? [];
    return { total: c.length, selected: c.filter((x) => x.selected).length, duplicates: c.filter((x) => x.status === "duplicate").length };
  }, [tl.data]);

  async function runNow() {
    setRunning(true);
    setRunErr(null);
    try {
      await api("/v1/system/run-cron", { method: "POST", body: {} });
      await Promise.all([status.reload(), tl.reload()]);
    } catch (e) {
      setRunErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <PageHeader icon={Radio} title="Live Monitor"
        subtitle={`Stream status for twitch.tv/${status.data?.target ?? "lacy"} and every moment viewers clipped — with why it was or wasn't selected.`}
        actions={<Button icon={RefreshCw} loading={running} onClick={runNow}>Check now</Button>} />
      {(status.error || tl.error) && <div className="mb-6"><ErrorState error={(status.error ?? tl.error)!} onRetry={status.reload} /></div>}
      {runErr && <div className="mb-6"><Notice tone="critical" title="Check failed">{runErr}</Notice></div>}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          {status.loading ? <Skeleton className="h-24 w-full" /> : (
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  {live ? <Badge tone="critical" icon={Radio}>Live</Badge> : <Badge tone="neutral">{st ? "Ended" : "Offline"}</Badge>}
                  {st?.ended_at && <span className="text-xs text-muted">ended {ago(st.ended_at)}</span>}
                </div>
                <p className="mt-3 truncate text-lg font-semibold">{st?.title ?? "Waiting for Lacy to go live"}</p>
                <p className="mt-1 text-sm text-muted">
                  {st ? `${st.game ?? "Unknown category"} · started ${dateTime(st.started_at)}` : "Checked every minute through the official Twitch API."}
                </p>
              </div>
              {tl.data && tl.data.streams.length > 1 && (
                <select className="h-9 rounded-lg border border-line-strong bg-card-2 px-2 text-sm" aria-label="Choose a stream"
                  value={st?.id ?? ""} onChange={(e) => { setStreamId(e.target.value); setSelected(null); }}>
                  {tl.data.streams.map((s) => <option key={s.id} value={s.id}>{dateTime(s.started_at)} · {s.title?.slice(0, 40) ?? "stream"}</option>)}
                </select>
              )}
            </div>
          )}
          <div className="mt-5">
            <Notice tone="info" title="How moments are found on the free plan">{status.data?.capabilities.how ??
              "Moments come from clips viewers create on Twitch, ranked by views."}</Notice>
          </div>
        </Card>

        <Card className="p-5">
          <p className="text-xs font-medium uppercase tracking-wider text-muted">Scheduler</p>
          <div className="mt-3 flex items-center gap-2">
            <Badge tone={status.data?.cron.alive ? "good" : "warn"} icon={Timer}>{status.data?.cron.alive ? "Running" : "Not running yet"}</Badge>
            <span className="text-xs text-muted">last run {ago(status.data?.cron.last_run)}</span>
          </div>
          <p className="mt-2 text-xs text-muted">Live check every minute · clips every 5 min while live, then for 48 h as viewers keep clipping.</p>
          <div className="mt-5 grid grid-cols-3 gap-2 text-center">
            {[["Viewer clips", counts.total], ["Selected", counts.selected], ["Duplicates", counts.duplicates]].map(([k, v]) => (
              <div key={k as string} className="rounded-lg border border-line bg-card-2 py-2">
                <p className="text-lg font-semibold tabular-nums">{v}</p><p className="text-[11px] text-muted">{k}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Moments on the stream timeline" hint="Height = views relative to the most viewed clip of this stream." />
          <div className="p-5">
            {tl.loading ? <Skeleton className="h-48 w-full" /> : points.length ? (
              <HighlightTimeline points={points} onSelect={setSelected} selected={sel?.id} title="Viewer clips by relative views over the stream" metric="views score" />
            ) : (
              <Empty icon={Activity} title="No viewer clips yet">
                {live ? "As soon as viewers clip moments, they appear here (checked every 5 minutes)." : "Moments appear here when Lacy streams and viewers clip."}
              </Empty>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Moment details" hint={sel ? `${clock(sel.offset_s)} into the stream · ${Math.round(sel.duration_s ?? 0)} s` : undefined} />
          <div className="space-y-4 p-5">
            {!sel ? <p className="text-sm text-muted">Select a moment on the timeline.</p> : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={(STATUS[sel.status]?.tone ?? "neutral") as Tone}>{STATUS[sel.status]?.text ?? sel.status}</Badge>
                  <span className="inline-flex items-center gap-1 text-xs text-muted"><Eye size={12} aria-hidden /> {num(sel.view_count)} views</span>
                  {sel.clipper && <span className="inline-flex items-center gap-1 text-xs text-muted"><Users size={12} aria-hidden /> {sel.clipper}</span>}
                </div>
                <p className="text-sm font-medium">{sel.title}</p>
                <p className="text-sm text-fg-2">{reason(sel, null)}</p>
                {sel.status_detail && sel.status !== "rejected" && <p className="text-xs text-[#fbc94a]">{sel.status_detail}</p>}
                {sel.twitch_url && <a className="text-xs text-[#a78bfa] hover:underline" href={sel.twitch_url} target="_blank" rel="noreferrer">Open on Twitch ↗</a>}
              </>
            )}
          </div>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="Event timeline" />
        <ul className="max-h-[22rem] divide-y divide-line overflow-auto">
          {tl.data?.events?.map((e, i) => (
            <li key={i} className="flex gap-3 px-5 py-3">
              <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${e.level === "ERROR" ? "bg-[#d03b3b]" : e.level === "WARNING" ? "bg-[#fab219]" : "bg-[#55555c]"}`} aria-hidden />
              <div className="min-w-0">
                <p className="text-sm text-fg-2">{e.message}</p>
                <p className="text-xs text-muted">{e.component} · {ago(e.at)}</p>
              </div>
            </li>
          ))}
          {tl.data && !tl.data.events?.length && <li><Empty icon={Activity} title="No events for this stream yet" /></li>}
        </ul>
      </Card>
    </>
  );
}
