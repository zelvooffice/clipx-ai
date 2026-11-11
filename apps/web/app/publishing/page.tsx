"use client";

import { motion } from "framer-motion";
import { CalendarClock, Inbox, Link2, Send, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, ErrorState, Notice, PageHeader, Skeleton, Toggle, type Tone, inputClass, linkButton } from "@/components/ui";
import { api, ApiError, mediaUrl, useApi } from "@/lib/api";
import { ago, dateTime, label } from "@/lib/format";
import type { PublishingJob, PublishingSettings, TikTokAccount } from "@/lib/types";

type Overview = {
  account: TikTokAccount | null; settings: PublishingSettings;
  capacity: { posts_24h: number; max_per_day: number; remaining_today: number; next_allowed_at: string | null } | null;
  compliance: { inbox_automation_consent: string; unaudited_restriction: string | null };
};
type History = { jobs: PublishingJob[]; events: { id: string; job_id: string; event: string; status: string; detail: Record<string, unknown>; at: string }[] };

const STATUS: Record<string, Tone> = {
  awaiting_confirmation: "warn", scheduled: "info", uploading: "info", processing: "info", sent_to_inbox: "good",
  published: "good", failed: "critical", cancelled: "neutral",
};

export default function PublishingPage() {
  const ov = useApi<Overview>("/v1/publishing/overview", { refreshMs: 20000 });
  const queue = useApi<PublishingJob[]>("/v1/publishing/queue", { refreshMs: 10000 });
  const hist = useApi<History>("/v1/publishing/history", { refreshMs: 30000 });
  const [confirming, setConfirming] = useState<PublishingJob | null>(null);
  const [consentOpen, setConsentOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const s = ov.data?.settings;

  async function saveSettings(patch: Record<string, unknown>) {
    setErr(null);
    try {
      await api("/v1/settings/publishing", { method: "PUT", body: patch });
      await ov.reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }
  async function cancel(id: string) {
    await api(`/v1/publishing/jobs/${id}/cancel`, { method: "POST", body: {} });
    queue.reload();
    hist.reload();
  }

  return (
    <>
      <PageHeader icon={Send} title="Publishing" subtitle="TikTok connection, automatic publishing and the posting queue." />
      {ov.error && <div className="mb-4"><ErrorState error={ov.error} onRetry={ov.reload} /></div>}
      {err && <div className="mb-4"><Notice tone="critical" title={err} /></div>}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5">
          <p className="text-xs font-medium uppercase tracking-wider text-muted">TikTok account</p>
          {ov.loading ? <Skeleton className="mt-4 h-12 w-full" /> : ov.data?.account ? (
            <div className="mt-4 flex items-center gap-3">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {ov.data.account.avatar_url && <img src={ov.data.account.avatar_url} alt="" className="size-11 rounded-full border border-line" />}
              <div className="min-w-0">
                <p className="truncate font-medium">{ov.data.account.display_name}</p>
                <p className="text-xs text-muted">@{ov.data.account.username ?? "—"} · {ov.data.account.scopes.join(", ")}</p>
              </div>
              {ov.data.account.revoked ? <Badge tone="critical">Reconnect</Badge> : <Badge tone="good">Connected</Badge>}
            </div>
          ) : (
            <div className="mt-4">
              <p className="text-sm text-fg-2">No TikTok account connected.</p>
              <Link href="/settings#tiktok" className={`${linkButton} accent-gradient mt-3 border-0 text-white`}><Link2 size={15} aria-hidden /> Connect TikTok</Link>
            </div>
          )}
        </Card>

        <Card className="p-5 lg:col-span-2">
          <div className="flex items-start justify-between gap-6">
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-muted">Auto-publish</p>
              <p className="mt-2 max-w-xl text-sm text-fg-2">
                Rendered, verified clips are captioned, scheduled and sent to TikTok automatically.
                {s?.auto_mode === "direct_post"
                  ? " TikTok requires your confirmation for each Direct Post — prepared posts wait in the queue for one click."
                  : " Drafts land in your TikTok inbox; you finish each post in the TikTok app."}
              </p>
            </div>
            {s && <div className="pt-1"><Toggle label={s.auto_publish ? "On" : "Off"} checked={s.auto_publish} disabled={!ov.data?.account}
              onChange={(v) => (v && !s.automation_consent_at ? setConsentOpen(true) : saveSettings({ auto_publish: v }))} /></div>}
          </div>
          {s && (
            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <select className={inputClass} value={s.auto_mode} aria-label="Publishing mode"
                onChange={(e) => saveSettings({ auto_mode: e.target.value })}>
                <option value="inbox_draft">Send drafts to TikTok inbox</option>
                <option value="direct_post">Direct Post (confirm each)</option>
              </select>
              <div className="rounded-lg border border-line bg-card-2 px-3 py-2 text-xs text-fg-2">
                {ov.data?.capacity ? `${ov.data.capacity.posts_24h}/${ov.data.capacity.max_per_day} posts in 24 h` : "—"}
              </div>
              <div className="rounded-lg border border-line bg-card-2 px-3 py-2 text-xs text-fg-2">
                Next allowed: {dateTime(ov.data?.capacity?.next_allowed_at)}
              </div>
            </div>
          )}
          {s?.automation_consent_at && (
            <p className="mt-3 text-xs text-muted">Automation consent recorded by {s.automation_consent_by} · {dateTime(s.automation_consent_at)}</p>
          )}
          {ov.data?.compliance.unaudited_restriction && (
            <div className="mt-4"><Notice tone="warn" title="TikTok app audit pending">{ov.data.compliance.unaudited_restriction}</Notice></div>
          )}
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="Queue" hint="Prepared posts, scheduled uploads and posts processing on TikTok" />
        {queue.loading ? <div className="p-5"><Skeleton className="h-24 w-full" /></div> : queue.data?.length ? (
          <ul className="divide-y divide-line">
            {queue.data.map((j) => (
              <li key={j.id} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={mediaUrl(j.clip.thumbnail_url) ?? ""} alt="" className="h-20 w-12 shrink-0 rounded-md bg-card-2 object-cover" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={STATUS[j.status] ?? "neutral"}>{label(j.status)}</Badge>
                    <Badge tone="neutral" icon={j.mode === "inbox_draft" ? Inbox : Send}>{j.mode === "inbox_draft" ? "Draft" : "Direct Post"}</Badge>
                  </div>
                  <p className="mt-1.5 line-clamp-1 text-sm">{j.title || j.clip.title}</p>
                  <p className="mt-0.5 flex items-center gap-1 text-xs text-muted">
                    <CalendarClock size={12} aria-hidden /> {j.scheduled_for ? dateTime(j.scheduled_for) : "As soon as allowed"}
                    {j.schedule_basis && <span className="truncate"> · {j.schedule_basis}</span>}
                  </p>
                </div>
                <div className="flex gap-2">
                  {j.status === "awaiting_confirmation" && <Button variant="primary" icon={ShieldCheck} onClick={() => setConfirming(j)}>Review & post</Button>}
                  {["awaiting_confirmation", "scheduled"].includes(j.status) && <Button variant="ghost" onClick={() => cancel(j.id)}>Cancel</Button>}
                </div>
              </li>
            ))}
          </ul>
        ) : <Empty icon={Send} title="Nothing in the queue">Rendered clips appear here when auto-publish is on, or when you queue them from Auto Clips.</Empty>}
      </Card>

      <Card className="mt-4">
        <CardHeader title="Publishing history" />
        {hist.data?.jobs.length ? (
          <ul className="divide-y divide-line">
            {hist.data.jobs.map((j) => (
              <li key={j.id} className="flex items-center gap-4 px-5 py-3">
                <Badge tone={STATUS[j.status] ?? "neutral"}>{label(j.status)}</Badge>
                <span className="min-w-0 flex-1 truncate text-sm text-fg-2">{j.title || j.clip.title}</span>
                {j.fail_reason && <span className="max-w-xs truncate text-xs text-[#f08a8a]" title={j.fail_reason}>{j.fail_reason}</span>}
                <span className="text-xs text-muted">{ago(j.published_at ?? j.created_at)}</span>
              </li>
            ))}
          </ul>
        ) : <Empty icon={CalendarClock} title="No publishing history yet" />}
      </Card>

      {confirming && <ConfirmCard job={confirming} onClose={() => setConfirming(null)} onDone={() => { setConfirming(null); queue.reload(); hist.reload(); }} />}
      {consentOpen && ov.data && (
        <ConsentDialog text={ov.data.compliance.inbox_automation_consent} mode={s?.auto_mode ?? "inbox_draft"}
          onCancel={() => setConsentOpen(false)}
          onAccept={async () => { setConsentOpen(false); await saveSettings({ auto_publish: true, automation_consent: true }); }} />
      )}
    </>
  );
}

function ConsentDialog({ text, mode, onCancel, onAccept }: { text: string; mode: string; onCancel: () => void; onAccept: () => void }) {
  const [ok, setOk] = useState(false);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Enable auto-publish">
      <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} className="w-full max-w-md rounded-2xl border border-line-strong bg-card p-6">
        <h2 className="font-semibold">Enable automatic publishing</h2>
        <p className="mt-2 text-sm text-fg-2">
          {mode === "direct_post"
            ? "CLIPX will prepare posts automatically. Each Direct Post still waits for your confirmation, as TikTok requires."
            : "CLIPX will automatically upload eligible clips to this TikTok account's inbox as drafts."}
        </p>
        <label className="mt-5 flex gap-3 rounded-xl border border-line bg-card-2 p-3.5 text-sm">
          <input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} className="mt-0.5 accent-[#8b5cf6]" />
          <span>{text}</span>
        </label>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" disabled={!ok} onClick={onAccept}>Enable</Button>
        </div>
      </motion.div>
    </div>
  );
}

type Confirmation = {
  job: PublishingJob;
  creator: { nickname: string; username: string; avatar_url: string; privacy_level_options: string[]; comment_disabled: boolean;
             duet_disabled: boolean; stitch_disabled: boolean; max_video_post_duration_sec: number | null };
  duration_ok: boolean; client_audited: boolean; consent_text: string; consent_text_branded: string; processing_notice: string;
};
const PRIVACY: Record<string, string> = {
  PUBLIC_TO_EVERYONE: "Everyone", MUTUAL_FOLLOW_FRIENDS: "Friends", FOLLOWER_OF_CREATOR: "Followers", SELF_ONLY: "Only me",
};

/** TikTok Content Sharing Guidelines — required UX for every Direct Post. */
function ConfirmCard({ job, onClose, onDone }: { job: PublishingJob; onClose: () => void; onDone: () => void }) {
  const { data, error, loading } = useApi<Confirmation>(`/v1/publishing/jobs/${job.id}/confirmation`);
  const [privacy, setPrivacy] = useState("");
  const [comment, setComment] = useState(false);
  const [duet, setDuet] = useState(false);
  const [stitch, setStitch] = useState(false);
  const [disclose, setDisclose] = useState(false);
  const [brandOrganic, setBrandOrganic] = useState(false);
  const [brandContent, setBrandContent] = useState(false);
  const [title, setTitle] = useState(job.title);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const disclosureIncomplete = disclose && !brandOrganic && !brandContent;
  const consentText = brandContent ? data?.consent_text_branded : data?.consent_text;
  const canPost = !!privacy && consent && !disclosureIncomplete && data?.duration_ok;

  async function post() {
    setBusy(true);
    setErrors(null);
    try {
      await api(`/v1/publishing/jobs/${job.id}/confirm`, { method: "POST", body: {
        privacy_level: privacy, allow_comment: comment, allow_duet: duet, allow_stitch: stitch, disclosure_on: disclose,
        brand_content: brandContent, brand_organic: brandOrganic, consent, title } });
      setDone(true);
    } catch (e) {
      setErrors(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/75 p-4" role="dialog" aria-modal="true" aria-label="Post to TikTok">
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
        className="flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-line-strong bg-card shadow-2xl">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h2 className="font-semibold">Post to TikTok</h2>
          <button onClick={onClose} className="rounded-lg p-1.5 text-muted hover:text-fg" aria-label="Close"><X size={16} /></button>
        </div>
        {error && <div className="p-6"><ErrorState error={error} /></div>}
        {loading && <div className="p-6"><Skeleton className="h-80 w-full" /></div>}
        {data && !done && (
          <div className="grid flex-1 gap-6 overflow-auto p-6 md:grid-cols-[220px_1fr]">
            <video src={mediaUrl(job.clip.video_url)} controls playsInline className="aspect-[9/16] w-full rounded-xl bg-black" aria-label="Preview of the video to post" />
            <div className="space-y-5">
              <div className="flex items-center gap-3 rounded-xl border border-line bg-card-2 p-3">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                {data.creator.avatar_url && <img src={data.creator.avatar_url} alt="" className="size-9 rounded-full" />}
                <div className="text-sm"><p className="text-muted">Posting to</p><p className="font-medium">{data.creator.nickname}</p></div>
              </div>
              {!data.duration_ok && <Notice tone="critical" title={`This video is longer than ${data.creator.max_video_post_duration_sec}s, the maximum for this account.`} />}
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-fg-2">Title</span>
                <textarea className={`${inputClass} h-20 py-2`} maxLength={2200} value={title} onChange={(e) => setTitle(e.target.value)} />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-fg-2">Who can view this video</span>
                <select className={inputClass} value={privacy} onChange={(e) => setPrivacy(e.target.value)} required>
                  <option value="" disabled>Select privacy</option>
                  {data.creator.privacy_level_options.map((p) => {
                    const blocked = (brandContent && p === "SELF_ONLY") || (!data.client_audited && p !== "SELF_ONLY");
                    return (
                      <option key={p} value={p} disabled={blocked}
                        title={brandContent && p === "SELF_ONLY" ? "Branded content visibility cannot be set to private." : undefined}>
                        {PRIVACY[p] ?? p}{!data.client_audited && p !== "SELF_ONLY" ? " (requires TikTok app audit)" : ""}
                      </option>
                    );
                  })}
                </select>
              </label>
              <fieldset>
                <legend className="mb-2 text-xs font-medium text-fg-2">Allow users to</legend>
                <div className="flex flex-wrap gap-4 text-sm">
                  {[["Comment", comment, setComment, data.creator.comment_disabled], ["Duet", duet, setDuet, data.creator.duet_disabled],
                    ["Stitch", stitch, setStitch, data.creator.stitch_disabled]].map(([l, v, setV, disabled]) => (
                    <label key={l as string} className={`flex items-center gap-2 ${disabled ? "cursor-not-allowed opacity-40" : ""}`}
                      title={disabled ? "Disabled in this account's TikTok privacy settings" : undefined}>
                      <input type="checkbox" className="accent-[#8b5cf6]" checked={v as boolean} disabled={disabled as boolean}
                        onChange={(e) => (setV as (b: boolean) => void)(e.target.checked)} />
                      {l as string}
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="space-y-3 rounded-xl border border-line p-4">
                <Toggle label="Disclose video content" description="Turn on if this video promotes yourself, a brand, product or service."
                  checked={disclose} onChange={(v) => { setDisclose(v); if (!v) { setBrandOrganic(false); setBrandContent(false); } }} />
                {disclose && (
                  <div className="space-y-2 pl-1 text-sm">
                    <label className="flex items-start gap-2"><input type="checkbox" className="mt-1 accent-[#8b5cf6]" checked={brandOrganic}
                      onChange={(e) => setBrandOrganic(e.target.checked)} />
                      <span>Your brand <span className="block text-xs text-muted">You are promoting yourself or your own business.</span></span></label>
                    <label className={`flex items-start gap-2 ${privacy === "SELF_ONLY" ? "opacity-40" : ""}`}
                      title={privacy === "SELF_ONLY" ? "Branded content visibility cannot be set to private." : undefined}>
                      <input type="checkbox" className="mt-1 accent-[#8b5cf6]" checked={brandContent} disabled={privacy === "SELF_ONLY"}
                        onChange={(e) => setBrandContent(e.target.checked)} />
                      <span>Branded content <span className="block text-xs text-muted">You are promoting another brand or a third party.</span></span></label>
                    {brandContent ? <p className="text-xs text-[#93c5fd]">Your video will be labeled as &apos;Paid partnership&apos;</p>
                      : brandOrganic ? <p className="text-xs text-[#93c5fd]">Your video will be labeled as &apos;Promotional content&apos;</p> : null}
                    {disclosureIncomplete && <p className="text-xs text-[#fbc94a]">You need to indicate if your content promotes yourself, a third party, or both.</p>}
                  </div>
                )}
              </div>
              <label className="flex gap-3 text-sm">
                <input type="checkbox" className="mt-1 accent-[#8b5cf6]" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                <span>{consentText}</span>
              </label>
              {errors && <Notice tone="critical" title="TikTok requirements not met">{errors}</Notice>}
            </div>
          </div>
        )}
        {done && data && (
          <div className="p-8 text-center">
            <ShieldCheck className="mx-auto text-[#5fd35f]" size={28} aria-hidden />
            <p className="mt-3 font-medium">Sent to TikTok</p>
            <p className="mt-1 text-sm text-muted">{data.processing_notice}</p>
          </div>
        )}
        <div className="flex justify-end gap-2 border-t border-line px-6 py-4">
          {done ? <Button variant="primary" onClick={onDone}>Done</Button> : <>
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button variant="primary" icon={Send} loading={busy} disabled={!canPost}
              title={disclosureIncomplete ? "You need to indicate if your content promotes yourself, a third party, or both." : undefined}
              onClick={post}>Post</Button>
          </>}
        </div>
      </motion.div>
    </div>
  );
}
