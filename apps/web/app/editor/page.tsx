"use client";

import { Clapperboard, Loader2, Play, Save, Scissors, Type, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, ErrorState, Field, Notice, PageHeader, Skeleton, Toggle, inputClass, linkButton } from "@/components/ui";
import { api, ApiError, mediaUrl, useApi } from "@/lib/api";
import { label, score } from "@/lib/format";
import type { Clip, ClipDetail, Cue } from "@/lib/types";

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
      <PageHeader icon={Clapperboard} title="Video Editor" subtitle="Choose a clip to fine-tune its subtitles, framing and edit." />
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
                    <span className="text-xs text-muted">{label(c.category)} · {label(c.edit_status)}</span>
                  </span>
                  <span className="text-xs tabular-nums text-muted">Q {score(c.quality_score)}</span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      ) : <Card><Empty icon={Clapperboard} title="No clips to edit yet" /></Card>}
    </>
  );
}

function ClipEditor({ id }: { id: string }) {
  const { data: clip, error, reload } = useApi<ClipDetail>(`/v1/clips/${id}`, { refreshMs: 6000 });
  const video = useRef<HTMLVideoElement>(null);
  const [cfg, setCfg] = useState<NonNullable<ClipDetail["edit_config"]> | null>(null);
  const [cues, setCues] = useState<Cue[]>([]);
  const [caption, setCaption] = useState("");
  const [tags, setTags] = useState("");
  const [msg, setMsg] = useState<{ tone: "good" | "critical"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [dirtyCues, setDirtyCues] = useState(false);
  const initFor = useRef<string | null>(null);

  useEffect(() => {
    if (!clip || initFor.current === `${clip.id}`) return;
    initFor.current = clip.id;
    setCfg(clip.edit_config ?? {
      layout: "auto", zoom_intensity: 1, sfx_enabled: true, crop_override: null, trim_start_s: 0, trim_end_s: null, version: 1, plan: null,
      subtitle_style: { size: 90, primary: "#FFE11A", highlight: "#FFFFFF", outline: 7, uppercase: false, emphasis: true },
    });
    setCues(clip.subtitles?.cues ?? []);
    setCaption(clip.caption ?? "");
    setTags((clip.hashtags ?? []).join(" "));
  }, [clip]);

  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!clip || !cfg) return <Skeleton className="h-[600px] w-full rounded-2xl" />;

  const rendering = ["queued", "rendering", "transcribing"].includes(clip.edit_status);
  const set = <K extends keyof typeof cfg>(k: K, v: (typeof cfg)[K]) => setCfg({ ...cfg, [k]: v });
  const style = cfg.subtitle_style;

  async function run(name: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(name);
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "good", text: ok });
      await reload();
    } catch (e) {
      setMsg({ tone: "critical", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  const saveAndRender = () => run("render", async () => {
    await api(`/v1/clips/${id}/edit-config`, { method: "PUT", body: {
      layout: cfg.layout, zoom_intensity: cfg.zoom_intensity, sfx_enabled: cfg.sfx_enabled,
      subtitle_style: { size: style.size, primary: style.primary, highlight: style.highlight, outline: style.outline,
                        uppercase: style.uppercase, emphasis: style.emphasis },
      ...(cfg.crop_override ? { crop_x_center: cfg.crop_override.x_center } : { clear_crop: true }),
      trim_start_s: cfg.trim_start_s, ...(cfg.trim_end_s ? { trim_end_s: cfg.trim_end_s } : {}),
    } });
    if (dirtyCues) {
      await api(`/v1/clips/${id}/subtitles`, { method: "PUT", body: { cues: cues.map((c) => ({
        start: c.start, end: c.end, zone: c.zone, text: c.words.map((w) => w.w).join(" "),
        emphasis: c.words.map((w, i) => (w.emph ? i : -1)).filter((i) => i >= 0) })) } });
      setDirtyCues(false);
    }
    await api(`/v1/clips/${id}/render`, { method: "POST", body: {} });
  }, "Saved. Re-rendering with your changes…");

  const saveCaption = () => run("caption", () => api(`/v1/clips/${id}`, {
    method: "PATCH", body: { caption, hashtags: tags.split(/\s+/).filter(Boolean) } }), "Caption saved.");

  return (
    <>
      <PageHeader icon={Clapperboard} title="Video Editor"
        subtitle={<span className="line-clamp-1">{clip.title}</span>}
        actions={<>
          <Link href="/editor" className={linkButton}>All clips</Link>
          <Button variant="primary" icon={Wand2} loading={busy === "render"} disabled={rendering || !clip.source_url && clip.media_source === "none"} onClick={saveAndRender}>
            Save & re-render
          </Button>
        </>} />
      {msg && <div className="mb-4"><Notice tone={msg.tone} title={msg.text} /></div>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,380px)_1fr]">
        {/* preview */}
        <div className="space-y-4">
          <Card className="overflow-hidden">
            <div className="relative mx-auto aspect-[9/16] w-full max-w-[380px] bg-black">
              {clip.video_url ? (
                <video ref={video} key={clip.video_url} src={mediaUrl(clip.video_url)} poster={mediaUrl(clip.thumbnail_url)}
                  controls playsInline className="size-full object-contain" aria-label="Rendered clip preview" />
              ) : (
                <div className="grid size-full place-items-center p-6 text-center text-sm text-muted">
                  {rendering ? <span className="flex items-center gap-2"><Loader2 className="animate-spin" size={16} /> Rendering…</span>
                    : clip.edit_error ?? "No rendered video yet."}
                </div>
              )}
            </div>
          </Card>
          <Card className="p-5">
            <p className="text-xs font-medium uppercase tracking-wider text-muted">Render status</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge tone={clip.edit_status === "rendered" ? "good" : rendering ? "info" : clip.edit_status === "failed" ? "critical" : "neutral"}>
                {label(clip.edit_status)}
              </Badge>
              <span className="text-xs text-muted">version {cfg.version} · layout {clip.layout ?? "—"}</span>
            </div>
            <ul className="mt-4 space-y-1.5 text-xs">
              {clip.processing.slice(-6).map((p, i) => (
                <li key={i} className="flex justify-between gap-3">
                  <span className="text-fg-2">{label(p.kind)}</span>
                  <span className={p.status === "dead" ? "text-[#f08a8a]" : "text-muted"}>{label(p.status)}{p.attempts > 1 ? ` · ${p.attempts} tries` : ""}</span>
                </li>
              ))}
            </ul>
            {clip.edit_config?.plan && (
              <p className="mt-4 text-xs text-muted">Edit plan: {clip.edit_config.plan.notes.join(" · ")}</p>
            )}
            {clip.rights_basis && <p className="mt-2 text-[11px] text-muted">Media source: {clip.rights_basis}</p>}
          </Card>
        </div>

        {/* controls */}
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Subtitles" hint="Bold yellow captions, synced word by word" />
              <div className="grid grid-cols-2 gap-4 p-5">
                <Field label={`Size · ${style.size}px`}>
                  <input type="range" min={60} max={130} value={style.size} className="w-full accent-[#8b5cf6]"
                    onChange={(e) => set("subtitle_style", { ...style, size: Number(e.target.value) })} />
                </Field>
                <Field label={`Outline · ${style.outline}px`}>
                  <input type="range" min={0} max={14} value={style.outline} className="w-full accent-[#8b5cf6]"
                    onChange={(e) => set("subtitle_style", { ...style, outline: Number(e.target.value) })} />
                </Field>
                <Field label="Text colour">
                  <input type="color" value={style.primary} className="h-9 w-full rounded-lg border border-line-strong bg-transparent"
                    onChange={(e) => set("subtitle_style", { ...style, primary: e.target.value.toUpperCase() })} />
                </Field>
                <Field label="Emphasis colour">
                  <input type="color" value={style.highlight} className="h-9 w-full rounded-lg border border-line-strong bg-transparent"
                    onChange={(e) => set("subtitle_style", { ...style, highlight: e.target.value.toUpperCase() })} />
                </Field>
                <div className="col-span-2 space-y-3">
                  <Toggle label="Emphasise key words" checked={style.emphasis} onChange={(v) => set("subtitle_style", { ...style, emphasis: v })} />
                  <Toggle label="ALL CAPS" checked={style.uppercase} onChange={(v) => set("subtitle_style", { ...style, uppercase: v })} />
                </div>
              </div>
            </Card>
            <Card>
              <CardHeader title="Framing & edit" hint="Layout, crop and pacing" />
              <div className="space-y-4 p-5">
                <Field label="Layout">
                  <select className={inputClass} value={cfg.layout} onChange={(e) => set("layout", e.target.value)}>
                    <option value="auto">Automatic (recommended)</option>
                    <option value="track">Face tracking</option>
                    <option value="stacked">Facecam over gameplay</option>
                    <option value="fit">Full frame, blurred background</option>
                  </select>
                </Field>
                <Field label={cfg.crop_override ? `Manual crop centre · ${Math.round(cfg.crop_override.x_center * 100)}%` : "Crop centre · automatic tracking"}>
                  <div className="flex items-center gap-3">
                    <input type="range" min={0} max={100} value={Math.round((cfg.crop_override?.x_center ?? 0.5) * 100)} className="w-full accent-[#8b5cf6]"
                      onChange={(e) => set("crop_override", { x_center: Number(e.target.value) / 100 })} />
                    {cfg.crop_override && <Button variant="ghost" onClick={() => set("crop_override", null)}>Auto</Button>}
                  </div>
                </Field>
                <Field label={`Punch-in intensity · ${cfg.zoom_intensity.toFixed(1)}×`} hint="0 disables dynamic zooms">
                  <input type="range" min={0} max={1.5} step={0.1} value={cfg.zoom_intensity} className="w-full accent-[#8b5cf6]"
                    onChange={(e) => set("zoom_intensity", Number(e.target.value))} />
                </Field>
                <Toggle label="Sound design accents" description="Subtle whoosh/pop on punch-ins (original, generated sounds)"
                  checked={cfg.sfx_enabled} onChange={(v) => set("sfx_enabled", v)} />
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Trim start (s)"><input type="number" step={0.1} min={0} className={inputClass} value={cfg.trim_start_s}
                    onChange={(e) => set("trim_start_s", Number(e.target.value))} /></Field>
                  <Field label="Trim end (s)"><input type="number" step={0.1} min={0} className={inputClass} value={cfg.trim_end_s ?? ""}
                    placeholder="end" onChange={(e) => set("trim_end_s", e.target.value ? Number(e.target.value) : null)} /></Field>
                </div>
              </div>
            </Card>
          </div>

          <Card>
            <CardHeader title={<span className="flex items-center gap-2"><Type size={14} aria-hidden /> Subtitle track</span>}
              hint={clip.subtitles ? `${clip.subtitles.engine} · ${clip.subtitles.word_count} words${clip.subtitles.edited ? " · edited" : ""}` : "Not transcribed yet"} />
            {cues.length ? (
              <ul className="max-h-80 divide-y divide-line overflow-auto">
                {cues.map((c, i) => (
                  <li key={i} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 px-5 py-2">
                    <button className="flex items-center gap-1 text-xs tabular-nums text-muted hover:text-fg"
                      onClick={() => { if (video.current) { video.current.currentTime = c.start; video.current.play(); } }}
                      aria-label={`Play from ${c.start.toFixed(1)} seconds`}>
                      <Play size={11} aria-hidden /> {c.start.toFixed(2)}
                    </button>
                    <input className={`${inputClass} h-8`} value={c.words.map((w) => w.w).join(" ")}
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
                      onChange={(e) => { const next = [...cues]; next[i] = { ...c, zone: e.target.value }; setCues(next); setDirtyCues(true); }}>
                      <option value="lower">Lower</option><option value="middle">Middle</option><option value="upper">Upper</option>
                    </select>
                  </li>
                ))}
              </ul>
            ) : <Empty icon={Scissors} title="No subtitles yet">Subtitles appear once the clip has been transcribed.</Empty>}
            {dirtyCues && <p className="border-t border-line px-5 py-2.5 text-xs text-[#fbc94a]">Unsaved subtitle edits — they are applied on “Save & re-render”.</p>}
          </Card>

          <Card>
            <CardHeader title="Caption & hashtags" hint={clip.caption_source ? `Generated by ${clip.caption_source}` : undefined}
              actions={<Button icon={Save} loading={busy === "caption"} onClick={saveCaption}>Save</Button>} />
            <div className="space-y-4 p-5">
              <Field label="Caption"><textarea className={`${inputClass} h-20 py-2`} maxLength={2000} value={caption} onChange={(e) => setCaption(e.target.value)} /></Field>
              <Field label="Hashtags" hint="#Lacy is always kept first. Up to 6 relevant tags."><input className={inputClass} value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
            </div>
          </Card>

          {clip.candidate && (
            <Card className="p-5">
              <p className="text-xs font-medium uppercase tracking-wider text-muted">Why this moment was clipped</p>
              <p className="mt-2 text-sm text-fg-2">{clip.candidate.explanation}</p>
              {clip.candidate.context_summary && <p className="mt-2 text-sm text-muted">{clip.candidate.context_summary}</p>}
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
