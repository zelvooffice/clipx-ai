"use client";

import { Clapperboard, Clock, ExternalLink, Eye, Film, Loader2, Lock, Send, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { useProcessor } from "@/components/Processor";
import { Badge, Card, cx, type Tone } from "@/components/ui";
import { mediaUrl } from "@/lib/api";
import { ago, label, num } from "@/lib/format";
import type { Clip } from "@/lib/types";

export const STATUS: Record<string, { tone: Tone; text: string }> = {
  rendered: { tone: "good", text: "Ready" },
  processing: { tone: "info", text: "Editing" },
  media_ready: { tone: "info", text: "Waiting for editor" },
  needs_media: { tone: "warn", text: "Needs authorization" },
  failed: { tone: "critical", text: "Edit failed" },
  rejected: { tone: "neutral", text: "Rejected" },
  duplicate: { tone: "neutral", text: "Duplicate" },
  discovered: { tone: "neutral", text: "Not selected" },
};

export function ClipCard({ clip, onQueue }: { clip: Clip; onQueue?: (c: Clip) => void }) {
  const vid = useRef<HTMLVideoElement>(null);
  const proc = useProcessor();
  const [startErr, setStartErr] = useState<string | null>(null);
  const st = STATUS[clip.status] ?? { tone: "neutral" as Tone, text: label(clip.status) };
  const editingHere = proc.current?.id === clip.id;
  const busy = clip.status === "processing" || editingHere;
  const canStart = (clip.status === "media_ready" || clip.status === "failed") && clip.source_url && !proc.current;

  async function start() {
    setStartErr(null);
    try {
      await proc.processNow(clip.id);
    } catch (e) {
      setStartErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Card interactive as="article" className="group overflow-hidden">
      <div
        className="relative aspect-[9/16] max-h-[340px] w-full overflow-hidden bg-[#09090b]"
        onMouseEnter={() => vid.current?.play().catch(() => undefined)}
        onMouseLeave={() => { if (vid.current) { vid.current.pause(); vid.current.currentTime = 0; } }}
      >
        {clip.video_url ? (
          <video ref={vid} src={mediaUrl(clip.video_url)} poster={mediaUrl(clip.thumbnail_url)} muted loop playsInline
            preload="none" className="size-full object-cover" aria-label={`Preview of ${clip.title}`} />
        ) : (
          <div className="grid size-full place-items-center text-muted">
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : clip.status === "needs_media" ? <Lock aria-hidden /> : <Film aria-hidden />}
          </div>
        )}
        <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between p-2.5">
          {clip.view_count !== null ? (
            <span className="inline-flex items-center gap-1 rounded-md bg-black/60 px-2 py-1 text-[11px] font-semibold tabular-nums text-white backdrop-blur"
              title="Views on Twitch (how CLIPX ranks moments)">
              <Eye size={11} aria-hidden /> {num(clip.view_count)}
            </span>
          ) : <span />}
          {clip.duration_s ? (
            <span className="rounded-md bg-black/60 px-2 py-1 text-[11px] tabular-nums text-white backdrop-blur">{Math.round(clip.duration_s)}s</span>
          ) : null}
        </div>
        {editingHere && proc.current?.progress.ratio !== undefined && (
          <div className="absolute inset-x-0 bottom-0 h-1 bg-black/50" aria-hidden>
            <div className="accent-gradient h-full transition-[width]" style={{ width: `${Math.round((proc.current.progress.ratio ?? 0) * 100)}%` }} />
          </div>
        )}
      </div>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={st.tone}>{editingHere ? `Editing in this tab · ${proc.current?.progress.stage}` : st.text}</Badge>
          {clip.origin === "viral_edit" && <Badge tone="accent" noIcon>Viral edit</Badge>}
        </div>
        <h3 className="line-clamp-2 text-sm font-medium leading-snug text-fg">{clip.title}</h3>
        {clip.clipper && <p className="-mt-2 text-[11px] text-muted">Clipped by {clip.clipper} on Twitch</p>}
        <p className={cx("line-clamp-2 text-xs", clip.caption ? "text-fg-2" : "text-muted")}>
          {clip.caption ? `${clip.caption} ${clip.hashtags.join(" ")}` : "The caption is written after editing."}
        </p>
        {clip.status_detail && clip.status !== "rendered" && <p className="line-clamp-3 text-xs text-[#fbc94a]">{clip.status_detail}</p>}
        {startErr && <p className="text-xs text-[#fca5a5]" role="alert">{startErr}</p>}
        <div className="flex items-center gap-2 pt-1">
          {canStart ? (
            <button onClick={start} className="accent-gradient inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg text-xs font-medium text-white">
              <Wand2 size={13} aria-hidden /> Edit now
            </button>
          ) : (
            <Link href={`/editor?clip=${clip.id}`} className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg border border-line-strong bg-card-2 text-xs font-medium hover:bg-[#1e1e22]">
              <Clapperboard size={13} aria-hidden /> Editor
            </Link>
          )}
          {onQueue && clip.status === "rendered" && (
            <button onClick={() => onQueue(clip)} className="accent-gradient inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg text-xs font-medium text-white">
              <Send size={13} aria-hidden /> Queue
            </button>
          )}
          {clip.twitch_url && (
            <a href={clip.twitch_url} target="_blank" rel="noreferrer" className="grid size-8 place-items-center rounded-lg border border-line-strong text-muted hover:text-fg" aria-label="Open the original Twitch clip">
              <ExternalLink size={13} />
            </a>
          )}
        </div>
        {clip.created_at && (
          <p className="flex items-center gap-1 text-[11px] text-muted"><Clock size={11} aria-hidden /> Clipped {ago(clip.created_at)}</p>
        )}
      </div>
    </Card>
  );
}
