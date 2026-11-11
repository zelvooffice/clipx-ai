"use client";

import { Activity, Eye, Gauge, MessageSquare, Mic, Radio, RefreshCw, Video } from "lucide-react";
import { useMemo, useState } from "react";
import { HBarList, HighlightTimeline } from "@/components/charts";
import { Badge, Button, Card, CardHeader, Empty, ErrorState, Notice, PageHeader, Skeleton, type Tone } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { ago, clock, dateTime, label, score } from "@/lib/format";
import type { Candidate, Capabilities, Session } from "@/lib/types";

type Source = { kind: string; status: string; detail: string | null; last_checked_at: string | null };
type Status = {
  live: Session | null; stage: string; monitor: Record<string, string | boolean> & { alive: boolean };
  sources: Source[]; capabilities: Capabilities;
};
type Timeline = {
  session: Session | null; events: { at: string; level: string; event: string; message: string }[];
  highlights: Candidate[]; latency_ms: { avg: number | null; max: number | null }; counts: Record<string, number>;
};

const SOURCE_TONE: Record<string, Tone> = { active: "good", available: "info", degraded: "warn", unavailable: "neutral" };
const SOURCE_NAME: Record<string, string> = {
  live_ingest: "Authorized live feed (RTMP/SRT)", twitch_native: "Twitch Create Clip",
  vod_clip: "Twitch clip from VOD (editor)", clip_media: "Twitch clip media download (editor)",
};
const WEIGHTS: Record<string, string> = {
  storytelling_context: "30%", visual_interest: "25%", audio_speech: "15%", audience_reaction: "15%", engagement_potential: "15%",
};
const SIGNAL_ICON = { video: Video, audio: Mic, speech: Mic, chat: MessageSquare } as const;

export default function LivePage() {
  const status = useApi<Status>("/v1/live/status", { refreshMs: 5000 });
  const tl = useApi<Timeline>("/v1/live/timeline", { refreshMs: 5000 });
  const [selected, setSelected] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const sess = tl.data?.session ?? status.data?.live ?? null;
  const live = !!status.data?.live;
  const points = useMemo(
    () => (tl.data?.highlights ?? []).map((h) => ({
      id: h.id, t: h.peak_offset_s, score: h.quality_score, status: h.status, category: h.category,
      label: h.title ?? label(h.category),
    })),
    [tl.data],
  );
  const sel = tl.data?.highlights.find((h) => h.id === selected) ?? tl.data?.highlights[0] ?? null;

  async function probe() {
    setProbing(true);
    try {
      await api("/v1/sources/probe", { method: "POST", body: {} });
      await status.reload();
    } finally {
      setProbing(false);
    }
  }

  return (
    <>
      <PageHeader icon={Radio} title="Live Monitor"
        subtitle="Real-time view of stream detection, analysis and highlight decisions."
        actions={<Button icon={RefreshCw} loading={probing} onClick={probe}>Re-check sources</Button>} />
      {(status.error || tl.error) && <div className="mb-6"><ErrorState error={(status.error ?? tl.error)!} onRetry={status.reload} /></div>}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          {status.loading ? <Skeleton className="h-24 w-full" /> : (
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  {live ? <Badge tone="critical" icon={Radio}>Live</Badge> : <Badge tone="neutral">Offline</Badge>}
                  {sess && <span className="text-xs text-muted">detected via {sess.detected_via}</span>}
                </div>
                <p className="mt-3 truncate text-lg font-semibold">{sess?.title ?? "Waiting for Lacy to go live"}</p>
                <p className="mt-1 text-sm text-muted">
                  {sess ? `${sess.game ?? "Unknown category"} · started ${dateTime(sess.started_at)}` : "No stream recorded yet"}
                </p>
                <p className="mt-4 text-sm"><span className="text-muted">Current stage: </span><span className="text-fg">{status.data?.stage}</span></p>
              </div>
              <div className="flex flex-wrap gap-2 sm:max-w-[14rem] sm:justify-end">
                {(sess?.signals?.length ? sess.signals : []).map((s) => {
                  const Icon = SIGNAL_ICON[s as keyof typeof SIGNAL_ICON] ?? Eye;
                  return <Badge key={s} tone="info" icon={Icon}>{label(s)}</Badge>;
                })}
                {sess && !sess.signals?.length && <Badge tone="warn">No analysable signal</Badge>}
              </div>
            </div>
          )}
          {sess?.analysis_mode === "chat_only" && (
            <div className="mt-5">
              <Notice tone="warn" title="Chat-only mode — no video analysis">
                No authorized video feed is connected, so CLIPX cannot see or hear the stream. Chat spikes only nominate
                moments; each one is verified on the clip&apos;s media before it can be used — or stays unverified if media
                cannot be obtained through an authorized method.
              </Notice>
            </div>
          )}
        </Card>

        <Card className="p-5">
          <p className="text-xs font-medium uppercase tracking-wider text-muted">Processing latency</p>
          <div className="mt-3 flex items-end gap-6">
            <div>
              <p className="text-3xl font-semibold tabular-nums">{tl.data?.latency_ms?.avg ?? "—"}<span className="ml-1 text-sm text-muted">ms</span></p>
              <p className="text-xs text-muted">average analysis time / moment</p>
            </div>
            <div><p className="text-lg tabular-nums text-fg-2">{tl.data?.latency_ms?.max ?? "—"} ms</p><p className="text-xs text-muted">max</p></div>
          </div>
          <div className="mt-5 grid grid-cols-3 gap-2 text-center">
            {[["Clipped", (tl.data?.counts?.clipped ?? 0) + (tl.data?.counts?.accepted ?? 0)], ["Rejected", tl.data?.counts?.rejected ?? 0],
              ["Duplicates", tl.data?.counts?.duplicate ?? 0]].map(([k, v]) => (
              <div key={k as string} className="rounded-lg border border-line bg-card-2 py-2">
                <p className="text-lg font-semibold tabular-nums">{v}</p><p className="text-[11px] text-muted">{k}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Detected highlights" hint="Every evaluated moment — including the ones CLIPX decided not to clip, and why." />
          <div className="p-5">
            {tl.loading ? <Skeleton className="h-48 w-full" /> : points.length ? (
              <HighlightTimeline points={points} onSelect={setSelected} selected={sel?.id} threshold={undefined} />
            ) : (
              <Empty icon={Activity} title="No moments evaluated yet">
                {live ? "Analysis is running — moments appear here as they are detected." : "Moments appear here during a live stream."}
              </Empty>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Moment details" hint={sel ? `${clock(sel.start_offset_s)}–${clock(sel.end_offset_s)} · ${sel.analyzer}` : undefined} />
          <div className="space-y-4 p-5">
            {!sel ? <p className="text-sm text-muted">Select a moment on the timeline.</p> : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={sel.status === "clipped" || sel.status === "accepted" ? "good" : sel.status === "provisional" ? "warn" : "neutral"}>
                    {label(sel.status)}
                  </Badge>
                  <Badge tone="accent" noIcon>{label(sel.category)}</Badge>
                  <span className="text-xs text-muted">quality {score(sel.quality_score)} · confidence {score(sel.confidence)}</span>
                </div>
                <p className="text-sm font-medium">{sel.title ?? "Untitled moment"}</p>
                <p className="text-sm text-fg-2">{sel.explanation}</p>
                {sel.reject_reason && <p className="text-xs text-[#fbc94a]">Not clipped: {sel.reject_reason}</p>}
                <HBarList title="Score components" max={1}
                  rows={Object.keys(WEIGHTS).map((k) => ({ label: label(k), weight: WEIGHTS[k], value: sel.components[k] ?? null }))}
                  note={`Supported by: ${sel.supporting_families.join(", ") || "no independent signal family"}`} />
              </>
            )}
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Video sources" hint="What CLIPX is authorized to use right now" />
          <ul className="divide-y divide-line">
            {status.data?.sources.map((s) => (
              <li key={s.kind} className="flex items-start justify-between gap-4 px-5 py-3.5">
                <div className="min-w-0">
                  <p className="text-sm text-fg">{SOURCE_NAME[s.kind] ?? s.kind}</p>
                  <p className="mt-0.5 text-xs text-muted">{s.detail}</p>
                </div>
                <Badge tone={SOURCE_TONE[s.status] ?? "neutral"}>{label(s.status)}</Badge>
              </li>
            ))}
            {status.data && !status.data.sources.length && <li><Empty icon={Gauge} title="Sources not probed yet" action={<Button onClick={probe}>Check now</Button>} /></li>}
          </ul>
          <div className="border-t border-line px-5 py-3 text-xs text-muted">
            Monitor: {status.data?.monitor.alive ? "running" : "not running"} · EventSub: {String(status.data?.monitor.eventsub ?? "—")}
            {" "}· Poller: {String(status.data?.monitor.poller ?? "—")}
          </div>
        </Card>
        <Card>
          <CardHeader title="Event timeline" />
          <ul className="max-h-[22rem] divide-y divide-line overflow-auto">
            {tl.data?.events?.map((e, i) => (
              <li key={i} className="flex gap-3 px-5 py-3">
                <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${e.level === "ERROR" ? "bg-[#d03b3b]" : e.level === "WARNING" ? "bg-[#fab219]" : "bg-[#55555c]"}`} aria-hidden />
                <div className="min-w-0">
                  <p className="text-sm text-fg-2">{e.message}</p>
                  <p className="text-xs text-muted">{label(e.event)} · {ago(e.at)}</p>
                </div>
              </li>
            ))}
            {tl.data && !tl.data.events?.length && <li><Empty icon={Activity} title="No events for this stream yet" /></li>}
          </ul>
        </Card>
      </div>
    </>
  );
}
