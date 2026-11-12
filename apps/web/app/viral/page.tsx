"use client";

import { motion } from "framer-motion";
import { ExternalLink, Flame, Loader2, Lock, RefreshCw, Send, Sparkles, TrendingUp, Wand2, X } from "lucide-react";
import { useState } from "react";
import { QueueDialog } from "@/components/QueueDialog";
import { Badge, Button, Card, CardHeader, Empty, ErrorState, Field, Notice, PageHeader, Skeleton, inputClass } from "@/components/ui";
import { api, ApiError, mediaUrl, useApi } from "@/lib/api";
import { ago, label, num } from "@/lib/format";
import type { Clip } from "@/lib/types";

type Source = {
  id: string; platform: string; url: string; title: string | null; creator: string | null; published_at: number | null;
  view_count: number | null; popularity: number; rights: string; duration: number | null;
};
type Suggestion = {
  template: string; name: string; description: string; min: number; max: number; evidence: string[];
  suggested_clip_ids: string[]; ready: boolean;
};
type Edit = {
  id: string; template: string; text: string | null; input_clip_ids: string[]; status: string; created_at: number;
  output_clip_id: string | null; out_title: string | null; video_url: string | null; thumbnail_url: string | null;
};
const TEXT_HINT: Record<string, string> = { pov: "you're in Lacy's chat when this happens", compilation: "Lacy's best moments this week", how_me_and_bro_look: "how me and bro look" };

/** Renders a queued edit in this tab from the input clips' rendered videos. */
async function renderEdit(e: Edit, onStage: (s: string) => void) {
  const clips = await Promise.all(e.input_clip_ids.map((id) => api<Clip>(`/v1/clips/${id}`)));
  const { renderViral } = await import("@/lib/render/engine");
  await renderViral({ editId: e.id, template: e.template, text: e.text ?? "",
    clips: clips.map((c) => ({ id: c.id, video_url: mediaUrl(c.video_url)!, duration_s: c.duration_s })) },
  (p) => onStage(`${p.stage}${p.ratio !== undefined && p.stage === "rendering" ? ` ${Math.round(p.ratio * 100)}%` : ""}`));
}

export default function ViralPage() {
  const sources = useApi<Source[]>("/v1/viral/sources");
  const sugg = useApi<Suggestion[]>("/v1/viral/templates");
  const edits = useApi<Edit[]>("/v1/viral/edits", { refreshMs: 10000 });
  const [creating, setCreating] = useState<Suggestion | null>(null);
  const [discovering, setDiscovering] = useState(false);

  async function discover() {
    setDiscovering(true);
    try {
      await api("/v1/viral/discover", { method: "POST", body: {} });
      await Promise.all([sources.reload(), sugg.reload()]);
    } finally {
      setDiscovering(false);
    }
  }

  return (
    <>
      <PageHeader icon={Flame} title="Viral Edits"
        subtitle="Learn from what already works for Lacy, then build new short-form formats from clips CLIPX is authorized to use."
        actions={<Button icon={RefreshCw} loading={discovering} onClick={discover}>Discover popular content</Button>} />

      <Notice tone="info" title="How sources are used">
        Popular Lacy clips are read for <strong>metadata only</strong> (title, views, date, creator) from Twitch&apos;s official API.
        New edits are composed in your browser exclusively from CLIPX&apos;s own rendered clips with authorized media — never from
        downloaded third-party videos.
      </Notice>

      {/* 1 — popular existing content */}
      <Card className="mt-6">
        <CardHeader title="Popular Lacy clips (last 30 days)" hint="Ranked by views, adjusted for age · view counts exactly as reported by Twitch" />
        {sources.error && <div className="p-5"><ErrorState error={sources.error} onRetry={sources.reload} /></div>}
        {sources.loading ? <div className="p-5"><Skeleton className="h-40 w-full" /></div> : sources.data?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  {["Title", "Platform", "Clipped by", "Published", "Views", "Popularity", "Media rights", ""].map((h) => (
                    <th key={h} className="px-5 py-2.5 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sources.data.slice(0, 30).map((s) => (
                  <tr key={s.id} className="border-b border-line last:border-0 hover:bg-card-2">
                    <td className="max-w-[22rem] truncate px-5 py-3 text-fg">{s.title ?? "Untitled"}</td>
                    <td className="px-5 py-3 text-fg-2">{label(s.platform)}</td>
                    <td className="px-5 py-3 text-fg-2">{s.creator ?? "—"}</td>
                    <td className="px-5 py-3 text-muted">{ago(s.published_at)}</td>
                    <td className="px-5 py-3 tabular-nums">{s.view_count === null ? <span className="text-muted">not provided</span> : num(s.view_count)}</td>
                    <td className="px-5 py-3 tabular-nums text-fg-2">{s.popularity.toFixed(2)}</td>
                    <td className="px-5 py-3">
                      {s.rights === "authorized"
                        ? <Badge tone="good">Authorized</Badge>
                        : <Badge tone="neutral" icon={Lock}>Metadata only</Badge>}
                    </td>
                    <td className="px-5 py-3">
                      <a href={s.url} target="_blank" rel="noreferrer" className="text-muted hover:text-fg" aria-label="Open source">
                        <ExternalLink size={14} />
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty icon={TrendingUp} title="No popular content discovered yet"
            action={<Button onClick={discover} loading={discovering}>Run discovery</Button>}>
            Discovery uses Twitch&apos;s official Get Clips API and runs automatically every 6 hours.
          </Empty>
        )}
      </Card>

      {/* 2 — trending formats */}
      <h2 className="mb-3 mt-10 flex items-center gap-2 text-sm font-medium text-fg-2"><Sparkles size={15} aria-hidden /> Trending formats</h2>
      {sugg.loading ? <Skeleton className="h-40 w-full rounded-2xl" /> : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {sugg.data?.map((s) => (
            <Card key={s.template} interactive className="flex flex-col p-5">
              <div className="flex items-start justify-between gap-3">
                <h3 className="font-medium">{s.name}</h3>
                {s.ready ? <Badge tone="good">Ready</Badge> : <Badge tone="neutral">Needs clips</Badge>}
              </div>
              <p className="mt-1.5 text-sm text-muted">{s.description}</p>
              <ul className="mt-4 flex-1 space-y-1.5 text-xs text-fg-2">
                {s.evidence.map((e) => <li key={e} className="flex gap-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-[#8d8d97]" aria-hidden />{e}</li>)}
              </ul>
              <Button className="mt-5" variant={s.ready ? "primary" : "secondary"} icon={Wand2} disabled={!s.ready} onClick={() => setCreating(s)}>
                Create {s.name} edit
              </Button>
            </Card>
          ))}
        </div>
      )}

      {/* 3 — generated edits */}
      <h2 className="mb-3 mt-10 flex items-center gap-2 text-sm font-medium text-fg-2"><Wand2 size={15} aria-hidden /> Generated edits</h2>
      {edits.data?.length ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {edits.data.map((e) => <EditCard key={e.id} e={e} onChange={edits.reload} />)}
        </div>
      ) : <Card><Empty icon={Wand2} title="No viral edits yet">Pick a ready format above to create one.</Empty></Card>}

      {creating && <CreateEdit s={creating} onClose={() => setCreating(null)} onDone={() => { setCreating(null); edits.reload(); }} />}
    </>
  );
}

function EditCard({ e, onChange }: { e: Edit; onChange: () => void }) {
  const [stage, setStage] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [queue, setQueue] = useState(false);
  async function render() {
    setErr(null);
    setStage("starting");
    try {
      await renderEdit(e, setStage);
      onChange();
    } catch (x) {
      setErr(x instanceof Error ? x.message : String(x));
    } finally {
      setStage(null);
    }
  }
  return (
    <Card className="overflow-hidden">
      <div className="relative aspect-[9/16] max-h-[340px] w-full bg-[#09090b]">
        {e.video_url ? (
          <video src={mediaUrl(e.video_url)} poster={mediaUrl(e.thumbnail_url)} controls playsInline preload="none"
            className="size-full object-cover" aria-label={`${label(e.template)} edit`} />
        ) : (
          <div className="grid size-full place-items-center p-4 text-center text-sm text-muted">
            {stage ? <span className="flex items-center gap-2" role="status"><Loader2 size={15} className="animate-spin" aria-hidden /> {stage}</span> : "Not rendered yet"}
          </div>
        )}
      </div>
      <div className="space-y-2 p-4">
        <div className="flex items-center gap-2">
          <Badge tone="accent" noIcon>{label(e.template)}</Badge>
          {e.status === "rendered" ? <Badge tone="good">Ready</Badge> : <Badge tone="info">{label(e.status)}</Badge>}
        </div>
        <p className="line-clamp-2 text-sm text-fg-2">{e.text || "—"}</p>
        <p className="text-[11px] text-muted">{e.input_clip_ids.length} source clip{e.input_clip_ids.length === 1 ? "" : "s"} · {ago(e.created_at)}</p>
        {err && <p className="text-xs text-[#fca5a5]" role="alert">{err}</p>}
        <div className="flex gap-2 pt-1">
          {e.status !== "rendered" && <Button variant="primary" icon={Wand2} loading={Boolean(stage)} onClick={render}>Render in this tab</Button>}
          {e.output_clip_id && <Button icon={Send} onClick={() => setQueue(true)}>Queue</Button>}
        </div>
      </div>
      {queue && e.output_clip_id && (
        <QueueDialog clip={{ id: e.output_clip_id, title: e.out_title ?? label(e.template) } as Clip}
          onClose={() => setQueue(false)} onDone={() => setQueue(false)} />
      )}
    </Card>
  );
}

function CreateEdit({ s, onClose, onDone }: { s: Suggestion; onClose: () => void; onDone: () => void }) {
  const clips = useApi<{ items: Clip[] }>("/v1/clips?status=rendered&origin=twitch_clip&limit=60&sort=views");
  const max = s.max;
  const [picked, setPicked] = useState<string[]>(s.suggested_clip_ids.slice(0, max));
  const [text, setText] = useState(TEXT_HINT[s.template] ?? "");
  const [stage, setStage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function toggle(id: string) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= max ? [...p.slice(1), id] : [...p, id]));
  }
  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const { id } = await api<{ id: string }>("/v1/viral/edits", { method: "POST", body: { template: s.template, clip_ids: picked, text } });
      await renderEdit({ id, template: s.template, text, input_clip_ids: picked } as Edit, setStage);
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
      setStage(null);
    }
  }
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label={`Create ${s.name}`}>
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
        className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-2xl border border-line-strong bg-card shadow-2xl">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h2 className="font-semibold">Create {s.name} edit</h2>
          <button onClick={onClose} className="rounded-lg p-1.5 text-muted hover:text-fg" aria-label="Close"><X size={16} /></button>
        </div>
        <div className="space-y-5 overflow-auto p-6">
          <Field label="Overlay text" hint="Shown in the template's banner. Keep it honest to what happens in the clips.">
            <input className={inputClass} value={text} maxLength={120} onChange={(e) => setText(e.target.value)} />
          </Field>
          <div>
            <p className="mb-2 text-xs font-medium text-fg-2">Clips ({picked.length}/{max})</p>
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-5">
              {clips.data?.items.map((c) => (
                <button key={c.id} onClick={() => toggle(c.id)} aria-pressed={picked.includes(c.id)}
                  className={`relative overflow-hidden rounded-lg border text-left ${picked.includes(c.id) ? "border-[#8b5cf6] ring-2 ring-[#8b5cf6]/40" : "border-line"}`}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={mediaUrl(c.thumbnail_url) ?? ""} alt="" className="aspect-[9/16] w-full object-cover" />
                  <span className="absolute inset-x-0 bottom-0 line-clamp-2 bg-black/70 p-1.5 text-[10px] text-white">{c.title}</span>
                </button>
              ))}
            </div>
            {clips.data && !clips.data.items.length && <p className="text-sm text-muted">No rendered clips with authorized media yet.</p>}
          </div>
          {stage && <Notice tone="info" title={`Rendering in this tab: ${stage}`}>Keep this tab open until it finishes.</Notice>}
          {err && <Notice tone="critical" title="Couldn’t create the edit">{err}</Notice>}
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-6 py-4">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={Wand2} loading={busy} disabled={picked.length < s.min} onClick={submit}>Render edit</Button>
        </div>
      </motion.div>
    </div>
  );
}
