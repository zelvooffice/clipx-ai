"use client";

import { Scissors, Search } from "lucide-react";
import { useState } from "react";
import { ClipCard } from "@/components/ClipCard";
import { QueueDialog } from "@/components/QueueDialog";
import { Button, Card, Empty, ErrorState, PageHeader, Skeleton, Tabs, inputClass } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Clip } from "@/lib/types";

type Filter = "all" | "rendered" | "media_ready" | "needs_media" | "failed" | "rejected";
const QUERY: Record<Filter, string> = {
  all: "", rendered: "&status=rendered", media_ready: "&status=media_ready", needs_media: "&status=needs_media",
  failed: "&status=failed", rejected: "&status=rejected",
};

export default function ClipsPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"new" | "views">("new");
  const [page, setPage] = useState(0);
  const [queueing, setQueueing] = useState<Clip | null>(null);
  const path = `/v1/clips?origin=twitch_clip&limit=24&offset=${page * 24}&sort=${sort}${QUERY[filter]}${q ? `&q=${encodeURIComponent(q)}` : ""}`;
  const { data, error, loading, reload } = useApi<{ total: number; items: Clip[] }>(path, { refreshMs: 15000 });

  return (
    <>
      <PageHeader icon={Scissors} title="Auto Clips"
        subtitle="The most viewed moments from Lacy's streams, picked from viewers' Twitch clips and edited to 1080×1920 with subtitles. Hover a card to preview." />
      <div className="mb-6 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <Tabs value={filter} onChange={(v) => { setFilter(v); setPage(0); }} options={[
          { value: "all", label: "All" }, { value: "rendered", label: "Ready" }, { value: "media_ready", label: "To edit" },
          { value: "needs_media", label: "Needs authorization" }, { value: "failed", label: "Failed" }, { value: "rejected", label: "Rejected" }]} />
        <div className="flex gap-2">
          <div className="relative w-full md:w-64">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
            <input className={`${inputClass} pl-8`} placeholder="Search titles & captions" value={q}
              onChange={(e) => { setQ(e.target.value); setPage(0); }} aria-label="Search clips" />
          </div>
          <select className={`${inputClass} w-36`} value={sort} onChange={(e) => setSort(e.target.value as "new" | "views")} aria-label="Sort">
            <option value="new">Newest</option><option value="views">Most viewed</option>
          </select>
        </div>
      </div>
      {error && <ErrorState error={error} onRetry={reload} />}
      {loading && !data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-[520px] rounded-2xl" />)}
        </div>
      ) : data && data.items.length ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
            {data.items.map((c) => <ClipCard key={c.id} clip={c} onQueue={setQueueing} />)}
          </div>
          <div className="mt-6 flex items-center justify-between text-sm text-muted">
            <span>{data.total} {data.total === 1 ? "clip" : "clips"}</span>
            <div className="flex gap-2">
              <Button variant="ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</Button>
              <Button variant="ghost" disabled={(page + 1) * 24 >= data.total} onClick={() => setPage((p) => p + 1)}>Next</Button>
            </div>
          </div>
        </>
      ) : data && (
        <Card><Empty icon={Scissors} title={q || filter !== "all" ? "No clips match these filters" : "No clips yet"}>
          {q || filter !== "all" ? "Try another filter." : "When Lacy streams, the moments viewers clip most are collected here (checked every 5 minutes) and edited in an open dashboard tab."}
        </Empty></Card>
      )}
      {queueing && <QueueDialog clip={queueing} onClose={() => setQueueing(null)} onDone={() => { setQueueing(null); reload(); }} />}
    </>
  );
}
