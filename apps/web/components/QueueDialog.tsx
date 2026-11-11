"use client";

import { motion } from "framer-motion";
import { Inbox, Send, X } from "lucide-react";
import { useState } from "react";
import { Button, Notice } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Clip } from "@/lib/types";

/** Choose how a clip goes to TikTok. Direct Post always needs the compliant confirmation card afterwards. */
export function QueueDialog({ clip, onClose, onDone }: { clip: Clip; onClose: () => void; onDone: () => void }) {
  const [mode, setMode] = useState<"direct_post" | "inbox_draft">("direct_post");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      await api(`/v1/publishing/clips/${clip.id}/queue`, { method: "POST", body: { mode } });
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Queue for TikTok">
      <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }}
        className="w-full max-w-md rounded-2xl border border-line-strong bg-card p-6 shadow-2xl">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-base font-semibold">Send to TikTok</h2>
            <p className="mt-1 line-clamp-1 text-sm text-muted">{clip.title}</p>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-muted hover:text-fg" aria-label="Close"><X size={16} /></button>
        </div>
        <div className="mt-5 space-y-2">
          {[
            { v: "direct_post" as const, icon: Send, t: "Direct Post", d: "Added to the queue; you confirm privacy, interactions and consent before it posts." },
            { v: "inbox_draft" as const, icon: Inbox, t: "Send as draft", d: "Uploaded now to the TikTok inbox; finish the post in the TikTok app." },
          ].map((o) => (
            <button key={o.v} onClick={() => setMode(o.v)} aria-pressed={mode === o.v}
              className={`flex w-full gap-3 rounded-xl border p-3.5 text-left transition-colors ${mode === o.v ? "border-[#8b5cf6]/60 bg-[#8b5cf6]/10" : "border-line hover:bg-card-2"}`}>
              <o.icon size={18} className="mt-0.5 shrink-0 text-fg-2" aria-hidden />
              <span><span className="block text-sm font-medium">{o.t}</span><span className="mt-0.5 block text-xs text-muted">{o.d}</span></span>
            </button>
          ))}
        </div>
        {mode === "inbox_draft" && (
          <p className="mt-3 text-xs text-muted">By sending, you consent to uploading this video to your TikTok inbox now.</p>
        )}
        {err && <div className="mt-4"><Notice tone="critical" title="Not queued">{err}</Notice></div>}
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} onClick={submit}>{mode === "inbox_draft" ? "Send draft" : "Add to queue"}</Button>
        </div>
      </motion.div>
    </div>
  );
}
