"use client";

import { Bell, Cpu, KeyRound, Link2, RotateCcw, Settings as SettingsIcon, Shield, Trash2, Tv as Twitch, Unplug } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, ErrorState, Field, Notice, PageHeader, Skeleton, Toggle, inputClass } from "@/components/ui";
import { api, ApiError, useApi } from "@/lib/api";
import { ago, label } from "@/lib/format";
import type { Capabilities, TikTokAccount } from "@/lib/types";

type TwitchStatus = {
  configured: boolean; target: { id?: string; login?: string; display_name?: string; profile_image_url?: string; error?: string } | null;
  accounts: { id: string; login: string; role: string; editor_verified: boolean | null; scopes: string[]; revoked: boolean; connected_at: string }[];
  capabilities: Capabilities; redirect_uri: string;
};
type TikTokStatus = { configured: boolean; account: TikTokAccount | null; scopes_requested: string[]; redirect_uri: string; pull_from_url_prefix: string | null };
type Processing = {
  weights: Record<string, number>; min_quality: number; min_confidence: number; target_clips_min: number; target_clips_max: number;
  min_clip_s: number; max_clip_s: number; mirror_as_twitch_clips: boolean; use_llm: boolean;
};
type AllSettings = { processing: Processing; notifications: { email_on_failure: boolean; email_on_stream_summary: boolean }; system: Record<string, unknown> };

const ROLES = [
  { role: "operator", title: "Operator account", desc: "Any Twitch account. Creates native clips (clips:edit) and reads chat as an audience signal." },
  { role: "editor", title: "Channel editor", desc: "An editor of Lacy's channel. Unlocks official clip downloads and precise clips from the VOD." },
  { role: "broadcaster", title: "Lacy (broadcaster)", desc: "Lacy's own account. Same media access as an editor, granted by the channel owner." },
];

export default function SettingsPage() {
  return <Suspense fallback={<Skeleton className="h-96 w-full" />}><SettingsInner /></Suspense>;
}

function SettingsInner() {
  const params = useSearchParams();
  const tw = useApi<TwitchStatus>("/v1/integrations/twitch");
  const tt = useApi<TikTokStatus>("/v1/integrations/tiktok");
  const all = useApi<AllSettings>("/v1/settings");
  const [msg, setMsg] = useState<{ tone: "good" | "critical"; text: string } | null>(null);

  useEffect(() => {
    const r = params.get("result");
    if (r === "connected") setMsg({ tone: "good", text: `${label(params.get("integration") ?? "")} connected.` });
    if (r === "error") setMsg({ tone: "critical", text: params.get("message") ?? "Connection failed" });
  }, [params]);

  async function act(fn: () => Promise<unknown>, ok?: string) {
    setMsg(null);
    try {
      await fn();
      if (ok) setMsg({ tone: "good", text: ok });
    } catch (e) {
      setMsg({ tone: "critical", text: e instanceof ApiError ? e.message : String(e) });
    }
  }
  const connect = (path: string, body: unknown) => act(async () => {
    const r = await api<{ authorize_url: string }>(path, { method: "POST", body });
    window.location.href = r.authorize_url;
  });

  return (
    <>
      <PageHeader icon={SettingsIcon} title="Settings" subtitle="Integrations, processing, security and notifications." />
      {msg && <div className="mb-4"><Notice tone={msg.tone} title={msg.text} /></div>}

      {/* Twitch */}
      <Card className="mb-4">
        <CardHeader title={<span className="flex items-center gap-2"><Twitch size={15} aria-hidden /> Twitch</span>}
          hint={tw.data?.target?.login ? `Monitoring twitch.tv/${tw.data.target.login} (id ${tw.data.target.id})` : tw.data?.target?.error} />
        <div className="space-y-5 p-5">
          {tw.error && <ErrorState error={tw.error} onRetry={tw.reload} />}
          {tw.data && !tw.data.configured && <Notice tone="warn" title="Twitch app credentials missing">Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET on the API server, and register this redirect URI in the Twitch developer console: <code className="text-fg">{tw.data.redirect_uri}</code></Notice>}
          <div className="grid gap-3 md:grid-cols-3">
            {ROLES.map((r) => (
              <div key={r.role} className="flex flex-col rounded-xl border border-line bg-card-2 p-4">
                <p className="text-sm font-medium">{r.title}</p>
                <p className="mt-1 flex-1 text-xs text-muted">{r.desc}</p>
                <Button className="mt-4" icon={Link2} disabled={!tw.data?.configured}
                  onClick={() => connect("/v1/integrations/twitch/connect", { role: r.role })}>Connect</Button>
              </div>
            ))}
          </div>
          {tw.data?.accounts.length ? (
            <ul className="divide-y divide-line rounded-xl border border-line">
              {tw.data.accounts.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <span className="font-medium">{a.login}</span>
                  <Badge tone="accent" noIcon>{label(a.role)}</Badge>
                  {a.revoked ? <Badge tone="critical">Reconnect needed</Badge> : <Badge tone="good">Active</Badge>}
                  {a.editor_verified === false && <Badge tone="warn">Not an editor of the channel</Badge>}
                  <span className="flex-1 truncate text-xs text-muted">{a.scopes.join(" · ")}</span>
                  <Button variant="ghost" icon={Unplug} onClick={() => act(async () => {
                    await api(`/v1/integrations/twitch/accounts/${a.id}`, { method: "DELETE" }); await tw.reload();
                  }, "Twitch account disconnected.")}>Disconnect</Button>
                </li>
              ))}
            </ul>
          ) : null}
          {tw.data && (
            <div className="grid gap-2 text-xs sm:grid-cols-3">
              {([["Live detection", tw.data.capabilities.can_detect_live], ["Native clips", tw.data.capabilities.can_create_clips],
                 ["Chat signal", tw.data.capabilities.can_read_chat], ["Clip media download", tw.data.capabilities.can_download_clips],
                 ["Precise VOD clips", tw.data.capabilities.can_vod_clip], ["Live video analysis", tw.data.capabilities.live_ingest_configured]] as const)
                .map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between rounded-lg border border-line px-3 py-2">
                    <span className="text-fg-2">{k}</span>
                    {v ? <Badge tone="good">Available</Badge> : <Badge tone="neutral">Unavailable</Badge>}
                  </div>
                ))}
            </div>
          )}
        </div>
      </Card>

      {/* TikTok */}
      <Card className="mb-4">
        <div id="tiktok" />
        <CardHeader title="TikTok" hint={tt.data ? `Scopes: ${tt.data.scopes_requested.join(", ")}` : undefined} />
        <div className="space-y-4 p-5">
          {tt.data && !tt.data.configured && <Notice tone="warn" title="TikTok app credentials missing">Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET on the API server and register this redirect URI in the TikTok developer portal: <code className="text-fg">{tt.data.redirect_uri}</code></Notice>}
          {tt.data?.account ? (
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-medium">{tt.data.account.display_name}</span>
              {tt.data.account.revoked ? <Badge tone="critical">Reconnect needed</Badge> : <Badge tone="good">Connected</Badge>}
              <span className="text-xs text-muted">token renews automatically · connected by {tt.data.account.connected_by}</span>
              <Button variant="ghost" icon={Unplug} onClick={() => act(async () => {
                await api(`/v1/integrations/tiktok/accounts/${tt.data!.account!.id}`, { method: "DELETE" }); await tt.reload();
              }, "TikTok disconnected.")}>Disconnect</Button>
            </div>
          ) : (
            <Button variant="primary" icon={Link2} disabled={!tt.data?.configured}
              onClick={() => connect("/v1/integrations/tiktok/connect", {})}>Connect TikTok account</Button>
          )}
          <p className="text-xs text-muted">
            Media transfer: {tt.data?.pull_from_url_prefix ? `PULL_FROM_URL from verified prefix ${tt.data.pull_from_url_prefix}` : "FILE_UPLOAD (verify an R2 domain prefix in TikTok to enable PULL_FROM_URL)"}
          </p>
        </div>
      </Card>

      {all.loading ? <Skeleton className="h-64 w-full rounded-2xl" /> : all.data && (
        <ProcessingCard initial={all.data.processing} onSaved={all.reload} act={act} />
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <SecurityCard act={act} />
        <Card>
          <CardHeader title={<span className="flex items-center gap-2"><Bell size={14} aria-hidden /> Notifications</span>}
            hint="Delivery channel is configured server-side" />
          <div className="space-y-4 p-5">
            {all.data && (["email_on_failure", "email_on_stream_summary"] as const).map((k) => (
              <Toggle key={k} label={k === "email_on_failure" ? "Email me when processing fails" : "Email a summary after each stream"}
                checked={all.data!.notifications[k]}
                onChange={(v) => act(async () => { await api("/v1/settings/notifications", { method: "PUT", body: { [k]: v } }); await all.reload(); })} />
            ))}
          </div>
          <CardHeader title={<span className="flex items-center gap-2"><Cpu size={14} aria-hidden /> System</span>} />
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 p-5 text-xs">
            {all.data && Object.entries(all.data.system).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{label(k)}</dt>
                <dd className="truncate text-fg-2">{typeof v === "boolean" ? (v ? "yes" : "no") : Array.isArray(v) ? v.join(", ") : String(v)}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>
      <JobsCard />
    </>
  );
}

const WEIGHT_LABELS: Record<string, string> = {
  storytelling_context: "Storytelling & context", visual_interest: "Visual interest", audio_speech: "Audio & speech",
  audience_reaction: "Audience reaction", engagement_potential: "Short-form engagement",
};

function ProcessingCard({ initial, onSaved, act }: { initial: Processing; onSaved: () => void; act: (f: () => Promise<unknown>, ok?: string) => void }) {
  const [p, setP] = useState(initial);
  const sum = Object.values(p.weights).reduce((a, b) => a + b, 0);
  const valid = Math.abs(sum - 1) < 0.011;
  return (
    <Card>
      <CardHeader title="Processing" hint="How CLIPX decides what becomes a clip"
        actions={<Button variant="primary" disabled={!valid} onClick={() => act(async () => {
          await api("/v1/settings/processing", { method: "PUT", body: p }); onSaved();
        }, "Processing settings saved.")}>Save</Button>} />
      <div className="grid gap-8 p-5 lg:grid-cols-2">
        <div className="space-y-4">
          <p className="text-xs font-medium uppercase tracking-wider text-muted">Scoring weights <span className={valid ? "text-muted" : "text-[#f08a8a]"}>· total {Math.round(sum * 100)}%</span></p>
          {Object.keys(WEIGHT_LABELS).map((k) => (
            <Field key={k} label={`${WEIGHT_LABELS[k]} · ${Math.round(p.weights[k] * 100)}%`}>
              <input type="range" min={0} max={60} value={Math.round(p.weights[k] * 100)} className="w-full accent-[#8b5cf6]"
                onChange={(e) => setP({ ...p, weights: { ...p.weights, [k]: Number(e.target.value) / 100 } })} />
            </Field>
          ))}
          {!valid && <p className="text-xs text-[#f08a8a]">Weights must add up to 100%.</p>}
        </div>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Minimum quality (0–100)"><input type="number" className={inputClass} min={0} max={100} value={Math.round(p.min_quality * 100)}
              onChange={(e) => setP({ ...p, min_quality: Number(e.target.value) / 100 })} /></Field>
            <Field label="Minimum confidence (0–100)"><input type="number" className={inputClass} min={0} max={100} value={Math.round(p.min_confidence * 100)}
              onChange={(e) => setP({ ...p, min_confidence: Number(e.target.value) / 100 })} /></Field>
            <Field label="Target clips / stream (min)"><input type="number" className={inputClass} value={p.target_clips_min}
              onChange={(e) => setP({ ...p, target_clips_min: Number(e.target.value) })} /></Field>
            <Field label="Max clips / stream" hint="Never padded to reach the minimum"><input type="number" className={inputClass} value={p.target_clips_max}
              onChange={(e) => setP({ ...p, target_clips_max: Number(e.target.value) })} /></Field>
            <Field label="Min clip length (s)"><input type="number" className={inputClass} value={p.min_clip_s}
              onChange={(e) => setP({ ...p, min_clip_s: Number(e.target.value) })} /></Field>
            <Field label="Max clip length (s)"><input type="number" className={inputClass} value={p.max_clip_s}
              onChange={(e) => setP({ ...p, max_clip_s: Number(e.target.value) })} /></Field>
          </div>
          <Toggle label="Use AI context analysis (Claude)" description="Falls back to a labelled heuristic when unavailable" checked={p.use_llm} onChange={(v) => setP({ ...p, use_llm: v })} />
          <Toggle label="Mirror highlights as Twitch clips" description="Creates an official Twitch clip for each accepted moment" checked={p.mirror_as_twitch_clips} onChange={(v) => setP({ ...p, mirror_as_twitch_clips: v })} />
        </div>
      </div>
    </Card>
  );
}

function SecurityCard({ act }: { act: (f: () => Promise<unknown>, ok?: string) => void }) {
  const emails = useApi<{ bootstrap: string[]; managed: { id: string; email: string; active: boolean }[]; note: string }>("/v1/settings/authorized-emails");
  const audit = useApi<{ at: string; actor: string; action: string; target: string | null }[]>("/v1/audit?limit=15");
  const [email, setEmail] = useState("");
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Shield size={14} aria-hidden /> Security</span>} hint={emails.data?.note} />
      <div className="space-y-4 p-5">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); act(async () => {
          await api("/v1/settings/authorized-emails", { method: "POST", body: { email } }); setEmail(""); await emails.reload();
        }, "Email authorized. Add it to the Cloudflare Access policy too."); }}>
          <input className={inputClass} type="email" required placeholder="teammate@example.com" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Email to authorize" />
          <Button type="submit" icon={KeyRound}>Authorize</Button>
        </form>
        <ul className="space-y-1.5 text-sm">
          {emails.data?.bootstrap.map((e) => <li key={e} className="flex items-center justify-between"><span>{e}</span><Badge tone="neutral" noIcon>Server config</Badge></li>)}
          {emails.data?.managed.filter((m) => m.active).map((m) => (
            <li key={m.id} className="flex items-center justify-between">
              <span>{m.email}</span>
              <Button variant="ghost" icon={Trash2} aria-label={`Remove ${m.email}`} onClick={() => act(async () => {
                await api(`/v1/settings/authorized-emails/${m.id}`, { method: "DELETE" }); await emails.reload();
              }, "Access removed.")} />
            </li>
          ))}
        </ul>
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Audit log</p>
          <ul className="max-h-56 space-y-1.5 overflow-auto text-xs">
            {audit.data?.map((a, i) => (
              <li key={i} className="flex gap-2"><span className="w-16 shrink-0 text-muted">{ago(a.at)}</span>
                <span className="truncate text-fg-2">{a.actor} · {a.action}{a.target ? ` · ${a.target}` : ""}</span></li>
            ))}
            {audit.data && !audit.data.length && <li className="text-muted">No changes recorded yet.</li>}
          </ul>
        </div>
      </div>
    </Card>
  );
}

function JobsCard() {
  const jobs = useApi<{ id: string; kind: string; ref_id: string | null; status: string; attempts: number; error: string | null; updated_at: string }[]>(
    "/v1/jobs?status=dead", { refreshMs: 30000 });
  if (!jobs.data?.length) return null;
  return (
    <Card className="mt-4">
      <CardHeader title="Failed background jobs" hint="Jobs that exhausted their retries. Retrying is safe — every step is idempotent." />
      {jobs.data.length ? (
        <ul className="divide-y divide-line">
          {jobs.data.map((j) => (
            <li key={j.id} className="flex items-center gap-4 px-5 py-3 text-sm">
              <Badge tone="critical">{label(j.kind)}</Badge>
              <span className="min-w-0 flex-1 truncate text-xs text-muted" title={j.error ?? ""}>{j.error}</span>
              <span className="text-xs text-muted">{ago(j.updated_at)}</span>
              <Button variant="ghost" icon={RotateCcw} onClick={async () => { await api(`/v1/jobs/${j.id}/retry`, { method: "POST", body: {} }); jobs.reload(); }}>Retry</Button>
            </li>
          ))}
        </ul>
      ) : <Empty title="No failed jobs" />}
    </Card>
  );
}
