"use client";

import { AlertTriangle, CheckCircle2, Clapperboard, Clock, ExternalLink, Film, Loader2, Send } from "lucide-react";
import Link from "next/link";
import { useRef } from "react";
import { Badge, Card, cx, type Tone } from "@/components/ui";
import { mediaUrl } from "@/lib/api";
import { label, score } from "@/lib/format";
import type { Clip } from "@/lib/types";

const EDIT: Record<string, { tone: Tone; text: string }> = {
  rendered: { tone: "good", text: "Ready" },
  rendering: { tone: "info", text: "Rendering" },
  transcribing: { tone: "info", text: "Transcribing" },
  queued: { tone: "info", text: "Queued" },
  needs_media: { tone: "warn", text: "Media unavailable" },
  failed: { tone: "critical", text: "Render failed" },
  rejected: { tone: "neutral", text: "Rejected" },
};

export function ClipCard({ clip, onQueue }: { clip: Clip; onQueue?: (c: Clip) => void }) {
  const vid = useRef<HTMLVideoElement>(null);
  const e = EDIT[clip.edit_status] ?? { tone: "neutral" as Tone, text: label(clip.edit_status) };
  const busy = ["rendering", "transcribing", "queued"].includes(clip.edit_status);
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
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Film aria-hidden />}
          </div>
        )}
        <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between p-2.5">
          <span className="rounded-md bg-black/60 px-2 py-1 text-[11px] font-semibold tabular-nums text-white backdrop-blur" title="Quality score">
            {score(clip.quality_score)}
          </span>
          {clip.duration_s && (
            <span className="rounded-md bg-black/60 px-2 py-1 text-[11px] tabular-nums text-white backdrop-blur">
              {Math.round(clip.duration_s)}s
            </span>
          )}
        </div>
      </div>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={e.tone}>{e.text}</Badge>
          <Badge tone="accent" noIcon>{label(clip.category)}</Badge>
          {clip.verification === "verified"
            ? <Badge tone="good" icon={CheckCircle2}>Verified</Badge>
            : <Badge tone="warn" icon={AlertTriangle}>Unverified</Badge>}
        </div>
        <h3 className="line-clamp-2 text-sm font-medium leading-snug text-fg">{clip.title}</h3>
        <p className={cx("line-clamp-2 text-xs", clip.caption ? "text-fg-2" : "text-muted")}>
          {clip.caption ? `${clip.caption} ${clip.hashtags.join(" ")}` : busy ? "Caption is generated after rendering." : "No caption yet."}
        </p>
        {clip.edit_error && <p className="line-clamp-2 text-xs text-[#fbc94a]">{clip.edit_error}</p>}
        <div className="flex items-center gap-2 pt-1">
          <Link href={`/editor?clip=${clip.id}`} className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg border border-line-strong bg-card-2 text-xs font-medium hover:bg-[#1e1e22]">
            <Clapperboard size={13} aria-hidden /> Edit
          </Link>
          {onQueue && clip.edit_status === "rendered" && (
            <button onClick={() => onQueue(clip)} className="accent-gradient inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-lg text-xs font-medium text-white">
              <Send size={13} aria-hidden /> Queue
            </button>
          )}
          {clip.twitch_clip_url && (
            <a href={clip.twitch_clip_url} target="_blank" rel="noreferrer" className="grid size-8 place-items-center rounded-lg border border-line-strong text-muted hover:text-fg" aria-label="Open Twitch clip">
              <ExternalLink size={13} />
            </a>
          )}
        </div>
        {clip.created_at && (
          <p className="flex items-center gap-1 text-[11px] text-muted"><Clock size={11} aria-hidden /> {new Date(clip.created_at).toLocaleString()}</p>
        )}
      </div>
    </Card>
  );
}
