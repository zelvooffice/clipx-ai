"use client";

import { Clapperboard, Loader2, Play, Save, Scissors, Type, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useProcessor } from "@/components/Processor";
import { STATUS } from "@/components/ClipCard";
import { Badge, Button, Card, CardHeader, Empty, ErrorState, Field, Notice, PageHeader, Skeleton, Toggle, inputClass, linkButton } from "@/components/ui";
import { api, ApiError, mediaUrl, useApi } from "@/lib/api";
import { num } from "@/lib/format";
import { buildCues } from "@/lib/render/subtitles";
import type { Clip, ClipDetail, ClipEdit, Cue } from "@/lib/types";

export default function EditorPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full rounded-2xl" />}>
      <Editor />
    </Suspense>
  );
}

function Editor() {
  const id = useSearchParams().get("clip");
  return id ? <ClipEditor id={id} /> : <Picker />;
}

function Picker() {
  const router = useRouter();
  const { data, loading } = useApi<{ items: Clip[] }>("/v1/clips?limit=40&sort=new");
  return (
    <>
      <PageHeader icon={Clapperboard} title="Video Editor" subtitle="Choose a clip to fine-tune its subtitles, framing and trim." />
      {loading ? <Skeleton className="h-64 w-full rounded-2xl" /> : data?.items.length ? (
        <Card>
          <ul className="divide-y divide-line">
            {data.items.map((c) => (
              <li key={c.id}>
                <button onClick={() => router.push(`/editor?clip=${c.id}`)} className="flex w-full items-center gap-4 px-5 py-3 text-left hover:bg-card-2">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={mediaUrl(c.thumbnail_url) ?? ""} alt="" className="h-14 w-8 shrink-0 rounded bg-card-2 object-cover" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">{c.title}</span>
                    <span className="text-xs text-muted">{STATUS[c.status]?.text ?? c.status}{c.origin === "viral_edit" ? " · viral edit" : ""}</span>
                  </span>
                  {c.view_count !== null && <span className="text-xs tabular-nums text-muted">{num(c.view_count)} views</span>}
                </button>
              </li>
            ))}
          </ul>
        </Card>
      ) : <Card><Empty icon={Clapperboard} title="No clips to edit yet" /></Card>}
    </>
  );
}

const DEFAULT_EDIT: Required<Omit<ClipEdit, "crop_x" | "trim_end">> & { crop_x: number | null; trim_end: number | null } = {
  layout: "fit", crop_x: null, zoom_intensity: 1, trim_start: 0, trim_end: null, sub_size: 90, sub_color: "#FFE11A",
  sub_highlight: "#FFFFFF", sub_uppercase: false,
};

function ClipEditor({ id }: { id: string }) {
  const { data: clip, error, reload } = useApi<ClipDetail>(`/v1/clips/${id}`, { refreshMs: 6000 });
  const proc = useProcessor();
  const video = useRef<HTMLVideoElement>(null);
  const [cfg, setCfg] = useState<typeof DEFAULT_EDIT | null>(null);
  const [cues, setCues] = useState<Cue[]>([]);
  const [caption, setCaption] = useState("");
  const [tags, setTags] = useState("");
  const [msg, setMsg] = useState<{ tone: "good" | "critical"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [dirtyCues, setDirtyCues] = useState(false);
  const initFor = useRef<string | null>(null);

  useEffect(() => {
    if (!clip) return;
    // words arrive after the first render (transcription); build the track then if it was empty
    if (initFor.current === clip.id && (cues.length || !clip.words.length)) return;
    if (initFor.current !== clip.id) {
      setCfg({ ...DEFAULT_EDIT, ...clip.edit } as typeof DEFAULT_EDIT);
      setCaption(clip.caption ?? "");
      setTags((clip.hashtags ?? []).join(" "));
    }
    initFor.current = clip.id;
    setCues(clip.cues_edited && clip.cues.length ? clip.cues : buildCues(clip.words) as Cue[]);
  }, [clip, cues.length]);

  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!clip || !cfg) return <Skeleton className="h-[600px] w-full rounded-2xl" />;

  const editingHere = proc.current?.id === clip.id;
  const rendering = clip.status === "processing" || editingHere;
  const set = <K extends keyof typeof cfg>(k: K, v: (typeof cfg)[K]) => setCfg({ ...cfg, [k]: v });
  const canRender = Boolean(clip.source_url) && clip.origin === "twitch_clip";

  async function run(name: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(name);
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "good", text: ok });
      await reload();
    } catch (e) {
      setMsg({ tone: "critical", text: e instanceof ApiError || e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  const saveAndRender = () => run("render", async () => {
    await api(`/v1/clips/${id}/edit`, { method: "PUT", body: cfg });
    if (dirtyCues) {
      await api(`/v1/clips/${id}/cues`, { method: "PUT", body: { cues: cues.map((c) => ({
        start: c.start, end: c.end, zone: c.zone, text: c.words.map((w) => w.w).join(" "),
        emphasis: c.words.map((w, i) => (w.emph ? i : -1)).filter((i) => i >= 0) })) } });
      setDirtyCues(false);
    }
    await api(`/v1/clips/${id}/rerender`, { method: "POST", body: {} });
    setMsg({ tone: "good", text: "Saved. Rendering in this tab — keep it open…" });
    await proc.processNow(id);
  }, "Re-rendered with your changes.");

  const saveCaption = () => run("caption", () => api(`/v1/clips/${id}`, {
    method: "PATCH", body: { caption, hashtags: tags.split(/\s+/).filter(Boolean) } }), "Caption saved.");

  const st = STATUS[clip.status];
  return (
    <>
      <PageHeader icon={Clapperboard} title="Video Editor"
        subtitle={<span className="line-clamp-1">{clip.title}</span>}
        actions={<>
          <Link href="/editor" className={linkButton}>All clips</Link>
          <Button variant="primary" icon={Wand2} loading={busy === "render"} disabled={rendering || !canRender || Boolean(proc.current)} onClick={saveAndRender}>
            Save & re-render
          </Button>
        </>} />
      {msg && <div className="mb-4"><Notice tone={msg.tone} title={msg.text} /></div>}
      {!canRender && clip.origin === "twitch_clip" && (
        <div className="mb-4"><Notice tone="warn" title="No source video yet">
          {clip.status_detail ?? "The original clip has not been downloaded through an authorized method yet."}</Notice></div>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,380px)_1fr]">
        <div className="space-y-4">
          <Card className="overflow-hidden">
            <div className="relative mx-auto aspect-[9/16] w-full max-w-[380px] bg-black">
              {clip.video_url && !editingHere ? (
                <video ref={video} key={clip.video_url} src={mediaUrl(clip.video_url)} poster={mediaUrl(clip.thumbnail_url)}
                  controls playsInline className="size-full object-contain" aria-label="Rendered clip preview" />
              ) : (
                <div className="grid size-full place-items-center p-6 text-center text-sm text-muted">
                  {rendering ? (
                    <span className="flex flex-col items-center gap-3" role="status">
                      <span className="flex items-center gap-2"><Loader2 className="animate-spin" size={16} aria-hidden />
                        {editingHere ? `${proc.current?.progress.stage}${proc.current?.progress.ratio !== undefined ? ` · ${Math.round((proc.current.progress.ratio ?? 0) * 100)}%` : ""}` : "Editing in another tab…"}</span>
                      {editingHere && <span className="text-xs">Keep this tab open until it finishes.</span>}
                    </span>
                  ) : clip.status_detail ?? "No rendered video yet."}
                </div>
              )}
            </div>
          </Card>
          <Card className="p-5">
            <p className="text-xs font-medium uppercase tracking-wider text-muted">Status</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge tone={st?.tone ?? "neutral"}>{st?.text ?? clip.status}</Badge>
              <span className="text-xs text-muted">version {clip.render_version}</span>
            </div>
            {clip.view_count !== null && <p className="mt-3 text-xs text-muted">{num(clip.view_count)} views on Twitch{clip.clipper ? ` · clipped by ${clip.clipper}` : ""}</p>}
            {clip.rights_basis && <p className="mt-2 text-[11px] text-muted">Media source: {clip.rights_basis}</p>}
            {clip.twitch_url && <a className="mt-2 inline-block text-xs text-[#a78bfa] hover:underline" href={clip.twitch_url} target="_blank" rel="noreferrer">Original clip on Twitch ↗</a>}
          </Card>
        </div>

        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Subtitles" hint="Bold yellow captions, synced word by word" />
              <div className="grid grid-cols-2 gap-4 p-5">
                <Field label={`Size · ${cfg.sub_size}px`}>
                  <input type="range" min={50} max={140} value={cfg.sub_size} className="w-full accent-[#8b5cf6]"
                    onChange={(e) => set("sub_size", Number(e.target.value))} />
                </Field>
                <div />
                <Field label="Text colour">
                  <input type="color" value={cfg.sub_color} className="h-9 w-full rounded-lg border border-line-strong bg-transparent"
                    onChange={(e) => set("sub_color", e.target.value.toUpperCase())} />
                </Field>
                <Field label="Emphasis colour">
                  <input type="color" value={cfg.sub_highlight} className="h-9 w-full rounded-lg border border-line-strong bg-transparent"
                    onChange={(e) => set("sub_highlight", e.target.value.toUpperCase())} />
                </Field>
                <div className="col-span-2">
                  <Toggle label="ALL CAPS" checked={cfg.sub_uppercase} onChange={(v) => set("sub_uppercase", v)} />
                </div>
              </div>
            </Card>
            <Card>
              <CardHeader title="Framing & edit" hint="Layout, crop and pacing" />
              <div className="space-y-4 p-5">
                <Field label="Layout">
                  <select className={inputClass} value={cfg.layout} onChange={(e) => set("layout", e.target.value as "fit" | "crop")}>
                    <option value="fit">Full frame, blurred background</option>
                    <option value="crop">Fill the screen (crop)</option>
                  </select>
                </Field>
                {cfg.layout === "crop" && (
                  <Field label={cfg.crop_x === null ? "Crop position · centre" : `Crop position · ${Math.round(cfg.crop_x * 100)}% from left`}>
                    <div className="flex items-center gap-3">
                      <input type="range" min={0} max={100} value={Math.round((cfg.crop_x ?? 0.5) * 100)} className="w-full accent-[#8b5cf6]"
                        aria-label="Crop position" onChange={(e) => set("crop_x", Number(e.target.value) / 100)} />
                      {cfg.crop_x !== null && <Button variant="ghost" onClick={() => set("crop_x", null)}>Centre</Button>}
                    </div>
                  </Field>
                )}
                <Field label={`Punch-in intensity · ${cfg.zoom_intensity.toFixed(1)}×`} hint="0 disables dynamic zooms">
                  <input type="range" min={0} max={1.5} step={0.1} value={cfg.zoom_intensity} className="w-full accent-[#8b5cf6]"
                    onChange={(e) => set("zoom_intensity", Number(e.target.value))} />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Trim start (s)"><input type="number" step={0.1} min={0} className={inputClass} value={cfg.trim_start}
                    onChange={(e) => set("trim_start", Math.max(0, Number(e.target.value)))} /></Field>
                  <Field label="Trim end (s)"><input type="number" step={0.1} min={0} className={inputClass} value={cfg.trim_end ?? ""}
                    placeholder="end" onChange={(e) => set("trim_end", e.target.value ? Number(e.target.value) : null)} /></Field>
                </div>
              </div>
            </Card>
          </div>

          <Card>
            <CardHeader title={<span className="flex items-center gap-2"><Type size={14} aria-hidden /> Subtitle track</span>}
              hint={clip.words.length ? `Workers AI Whisper · ${clip.words.length} words${clip.cues_edited ? " · edited" : ""}` : "Transcribed on the first render"} />
            {cues.length ? (
              <ul className="max-h-80 divide-y divide-line overflow-auto">
                {cues.map((c, i) => (
                  <li key={i} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 px-5 py-2">
                    <button className="flex items-center gap-1 text-xs tabular-nums text-muted hover:text-fg"
                      onClick={() => { if (video.current) { video.current.currentTime = Math.max(0, c.start - cfg.trim_start); video.current.play(); } }}
                      aria-label={`Play from ${c.start.toFixed(1)} seconds`}>
                      <Play size={11} aria-hidden /> {c.start.toFixed(2)}
                    </button>
                    <input className={`${inputClass} h-8`} value={c.words.map((w) => w.w).join(" ")} maxLength={80}
                      aria-label={`Subtitle ${i + 1}`}
                      onChange={(e) => {
                        const words = e.target.value.split(" ");
                        const step = (c.end - c.start) / Math.max(1, words.length);
                        const next = [...cues];
                        next[i] = { ...c, words: words.map((w, j) => ({ w, start: c.start + j * step, end: c.start + (j + 1) * step, emph: c.words[j]?.emph ?? false })) };
                        setCues(next);
                        setDirtyCues(true);
                      }} />
                    <select className="h-8 rounded-lg border border-line-strong bg-[#0b0b0d] px-2 text-xs" value={c.zone} aria-label="Position"
                      onChange={(e) => { const next = [...cues]; next[i] = { ...c, zone: e.target.value as Cue["zone"] }; setCues(next); setDirtyCues(true); }}>
                      <option value="lower">Lower</option><option value="middle">Middle</option><option value="upper">Upper</option>
                    </select>
                  </li>
                ))}
              </ul>
            ) : <Empty icon={Scissors} title="No subtitles yet">Speech is transcribed with Workers AI the first time the clip is edited.</Empty>}
            {dirtyCues && <p className="border-t border-line px-5 py-2.5 text-xs text-[#fbc94a]">Unsaved subtitle edits — they are applied on “Save & re-render”. Empty cues are not allowed.</p>}
          </Card>

          <Card>
            <CardHeader title="Caption & hashtags" hint={clip.caption_source ? `Source: ${clip.caption_source}` : undefined}
              actions={<Button icon={Save} loading={busy === "caption"} onClick={saveCaption}>Save</Button>} />
            <div className="space-y-4 p-5">
              <Field label="Caption"><textarea className={`${inputClass} h-20 py-2`} maxLength={2000} value={caption} onChange={(e) => setCaption(e.target.value)} /></Field>
              <Field label="Hashtags" hint="#Lacy is always kept first. Up to 6 relevant tags."><input className={inputClass} value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
