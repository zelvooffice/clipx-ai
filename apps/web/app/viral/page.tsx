"use client";

import { motion } from "framer-motion";
import { ExternalLink, Flame, Lock, RefreshCw, Sparkles, TrendingUp, Wand2, X } from "lucide-react";
import { useState } from "react";
import { ClipCard } from "@/components/ClipCard";
import { Badge, Button, Card, CardHeader, Empty, ErrorState, Field, Notice, PageHeader, Skeleton, inputClass } from "@/components/ui";
import { api, ApiError, mediaUrl, useApi } from "@/lib/api";
import { ago, label, num } from "@/lib/format";
import type { Clip } from "@/lib/types";

type Source = {
  id: string; platform: string; url: string; title: string | null; creator: string | null; published_at: string | null;
  view_count: number | null; engagement: Record<string, number> | null; popularity_score: number; rights_status: string;
  discovered_via: string; duration_s: number | null;
};
type Suggestion = {
  template: string; name: string; description: string; score: number; evidence: string[];
  suggested_clip_ids: string[]; ready: boolean; text_hint: string;
};
type Edit = { id: string; template: string; params: { text?: string }; status: string; error: string | null; created_at: string; output: Clip | null };

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
      setTimeout(() => { sources.reload(); sugg.reload(); }, 4000);
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
        Popular videos are read for <strong>metadata only</strong> (title, views, date, creator) from official APIs. New edits are
        composed exclusively from CLIPX&apos;s own clips with authorized media — never from downloaded third-party videos.
      </Notice>

      {/* 1 — popular existing content */}
      <Card className="mt-6">
        <CardHeader title="Popular Lacy videos" hint="Existing content ranked by views per day · statistics exactly as reported by the source" />
        {sources.error && <div className="p-5"><ErrorState error={sources.error} onRetry={sources.reload} /></div>}
        {sources.loading ? <div className="p-5"><Skeleton className="h-40 w-full" /></div> : sources.data?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  {["Title", "Platform", "Creator", "Published", "Views", "Popularity", "Media rights", ""].map((h) => (
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
                    <td className="px-5 py-3 tabular-nums text-fg-2">{s.popularity_score.toFixed(2)}</td>
                    <td className="px-5 py-3">
                      {s.rights_status === "authorized"
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
            Discovery uses Twitch&apos;s official Get Clips API (and YouTube&apos;s Data API if a key is configured).
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
          {edits.data.map((e) => e.output ? <ClipCard key={e.id} clip={e.output} /> : (
            <Card key={e.id} className="p-5">
              <p className="font-medium">{label(e.template)}</p>
              <p className="mt-1 text-sm text-muted">{e.params.text || "—"}</p>
              <div className="mt-3">{e.status === "failed" ? <Badge tone="critical">Failed</Badge> : <Badge tone="info">{label(e.status)}</Badge>}</div>
              {e.error && <p className="mt-2 line-clamp-3 text-xs text-[#f08a8a]">{e.error}</p>}
            </Card>
          ))}
        </div>
      ) : <Card><Empty icon={Wand2} title="No viral edits yet">Pick a ready format above to create one.</Empty></Card>}

      {creating && <CreateEdit s={creating} onClose={() => setCreating(null)} onDone={() => { setCreating(null); edits.reload(); }} />}
    </>
  );
}

function CreateEdit({ s, onClose, onDone }: { s: Suggestion; onClose: () => void; onDone: () => void }) {
  const clips = useApi<{ items: Clip[] }>("/v1/clips?status=rendered&verification=verified&origin=stream_clip&limit=60&sort=quality");
  const max = s.template === "compilation" ? 5 : ["pov", "storytime"].includes(s.template) ? 1 : 2;
  const [picked, setPicked] = useState<string[]>(s.suggested_clip_ids.slice(0, max));
  const [text, setText] = useState(s.text_hint);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function toggle(id: string) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= max ? [...p.slice(1), id] : [...p, id]));
  }
  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      await api("/v1/viral/edits", { method: "POST", body: { template: s.template, clip_ids: picked, text } });
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
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
            {clips.data && !clips.data.items.length && <p className="text-sm text-muted">No verified, rendered clips with authorized media yet.</p>}
          </div>
          {err && <Notice tone="critical" title="Couldn’t create the edit">{err}</Notice>}
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-6 py-4">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={Wand2} loading={busy} disabled={!picked.length} onClick={submit}>Render edit</Button>
        </div>
      </motion.div>
    </div>
  );
}
