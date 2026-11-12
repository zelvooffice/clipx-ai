"use client";

import { Cpu, Link2, Settings as SettingsIcon, Shield, Timer, Tv as Twitch, Unplug } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Badge, Button, Card, CardHeader, ErrorState, Field, Notice, PageHeader, Skeleton, Toggle, inputClass } from "@/components/ui";
import { api, ApiError, setUnlockToken, useApi } from "@/lib/api";
import { ago, label } from "@/lib/format";
import type { Activity, Capabilities, ProcessingSettings, TikTokAccount } from "@/lib/types";

type TwitchStatus = {
  configured: boolean; target: { id?: string; login?: string; display_name?: string; profile_image_url?: string; error?: string } | null;
  accounts: { user_id: string; login: string; role: string; editor_verified: number | null; scopes: string[]; revoked: boolean }[];
  capabilities: Capabilities; redirect_uri: string;
};
type TikTokStatus = { configured: boolean; account: TikTokAccount | null; scopes_requested: string[]; redirect_uri: string };
type System = { plan: string; twitch_app_configured: boolean; twitch_target_login: string; tiktok_app_configured: boolean; workers_ai: boolean;
  password_required: boolean; password_in_dashboard: boolean; access_configured: boolean; authorized_emails: string[]; twitch_redirect_uri: string; tiktok_redirect_uri: string;
  config_problems: string[] };
type AllSettings = { processing: ProcessingSettings; system: System };

const ROLES = [
  { role: "operator", title: "Operator account", desc: "Any Twitch account. Live status and viewer clips work without it; it only identifies who operates CLIPX." },
  { role: "editor", title: "Channel editor", desc: "An editor of Lacy's channel. Unlocks official clip downloads (Get Clips Download), so clips can be edited." },
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
      <PageHeader icon={SettingsIcon} title="Settings" subtitle="Integrations, processing, security and the scheduler." />
      {msg && <div className="mb-4"><Notice tone={msg.tone} title={msg.text} /></div>}

      {/* Twitch */}
      <Card className="mb-4">
        <CardHeader title={<span className="flex items-center gap-2"><Twitch size={15} aria-hidden /> Twitch</span>}
          hint={tw.data?.target?.login ? `Monitoring twitch.tv/${tw.data.target.login} (id ${tw.data.target.id})` : tw.data?.target?.error} />
        <div className="space-y-5 p-5">
          {tw.error && <ErrorState error={tw.error} onRetry={tw.reload} />}
          {tw.data && !tw.data.configured && <Notice tone="warn" title="Twitch app credentials missing">Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET as Worker secrets in Cloudflare, and register this redirect URI in the Twitch developer console: <code className="text-fg">{tw.data.redirect_uri}</code></Notice>}
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
                <li key={a.user_id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <span className="font-medium">{a.login}</span>
                  <Badge tone="accent" noIcon>{label(a.role)}</Badge>
                  {a.revoked ? <Badge tone="critical">Reconnect needed</Badge> : <Badge tone="good">Active</Badge>}
                  {a.editor_verified === 0 && <Badge tone="warn">Not an editor of the channel</Badge>}
                  {a.editor_verified === 1 && <Badge tone="good">Media access verified</Badge>}
                  <span className="flex-1 truncate text-xs text-muted">{a.scopes.join(" · ")}</span>
                  <Button variant="ghost" icon={Unplug} onClick={() => act(async () => {
                    await api(`/v1/integrations/twitch/accounts/${a.user_id}`, { method: "DELETE" }); await tw.reload();
                  }, "Twitch account disconnected.")}>Disconnect</Button>
                </li>
              ))}
            </ul>
          ) : null}
          {tw.data && (
            <div className="grid gap-2 text-xs sm:grid-cols-3">
              {([["Live detection", tw.data.capabilities.can_detect_live], ["Viewer clips", tw.data.capabilities.can_list_clips],
                 ["Clip media download", tw.data.capabilities.can_download_clips]] as const)
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
          {tt.data && !tt.data.configured && <Notice tone="warn" title="TikTok app credentials missing">Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET as Worker secrets in Cloudflare and register this redirect URI in the TikTok developer portal: <code className="text-fg">{tt.data.redirect_uri}</code></Notice>}
          {tt.data?.account ? (
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-medium">{tt.data.account.display_name}</span>
              {tt.data.account.revoked ? <Badge tone="critical">Reconnect needed</Badge> : <Badge tone="good">Connected</Badge>}
              <span className="text-xs text-muted">token renews automatically · connected by {tt.data.account.connected_by}</span>
              <Button variant="ghost" icon={Unplug} onClick={() => act(async () => {
                await api(`/v1/integrations/tiktok/accounts/${tt.data!.account!.open_id}`, { method: "DELETE" }); await tt.reload();
              }, "TikTok disconnected.")}>Disconnect</Button>
            </div>
          ) : (
            <Button variant="primary" icon={Link2} disabled={!tt.data?.configured}
              onClick={() => connect("/v1/integrations/tiktok/connect", {})}>Connect TikTok account</Button>
          )}
          <p className="text-xs text-muted">Media transfer: FILE_UPLOAD, streamed from R2 by the Worker (videos up to 64 MB in one chunk).</p>
        </div>
      </Card>

      {all.loading ? <Skeleton className="h-64 w-full rounded-2xl" /> : all.data && (
        <ProcessingCard initial={all.data.processing} onSaved={all.reload} act={act} />
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <SecurityCard system={all.data?.system ?? null} />
        <SchedulerCard system={all.data?.system ?? null} />
      </div>
    </>
  );
}

function ProcessingCard({ initial, onSaved, act }: { initial: ProcessingSettings; onSaved: () => void; act: (f: () => Promise<unknown>, ok?: string) => void }) {
  const [p, setP] = useState(initial);
  const num = (k: keyof ProcessingSettings) => (e: React.ChangeEvent<HTMLInputElement>) => setP({ ...p, [k]: Number(e.target.value) });
  return (
    <Card>
      <CardHeader title="Processing" hint="Which viewer clips become CLIPX clips, and how they are edited"
        actions={<Button variant="primary" onClick={() => act(async () => {
          await api("/v1/settings/processing", { method: "PUT", body: p }); onSaved();
        }, "Processing settings saved.")}>Save</Button>} />
      <div className="grid gap-8 p-5 lg:grid-cols-2">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Max clips / stream" hint="The best moments up to this number — never padded">
            <input type="number" min={1} max={40} className={inputClass} value={p.target_clips_max} onChange={num("target_clips_max")} /></Field>
          <Field label="Minimum views" hint="Viewer clips with fewer views are ignored">
            <input type="number" min={0} className={inputClass} value={p.min_views} onChange={num("min_views")} /></Field>
          <Field label="Min clip length (s)"><input type="number" min={5} max={60} className={inputClass} value={p.min_clip_s} onChange={num("min_clip_s")} /></Field>
          <Field label="Max clip length (s)"><input type="number" min={5} max={60} className={inputClass} value={p.max_clip_s} onChange={num("max_clip_s")} /></Field>
        </div>
        <div className="space-y-4">
          <Field label="Default layout">
            <select className={inputClass} value={p.default_layout} onChange={(e) => setP({ ...p, default_layout: e.target.value as "fit" | "crop" })}>
              <option value="fit">Full frame, blurred background</option>
              <option value="crop">Fill the screen (crop)</option>
            </select>
          </Field>
          <Field label={`Default punch-in intensity · ${p.zoom_intensity.toFixed(1)}×`}>
            <input type="range" min={0} max={1.5} step={0.1} value={p.zoom_intensity} className="w-full accent-[#8b5cf6]"
              onChange={(e) => setP({ ...p, zoom_intensity: Number(e.target.value) })} />
          </Field>
          <Toggle label="Edit new clips automatically" description="Any open, unlocked dashboard tab edits waiting clips in the background (the free plan has no server-side video processing)."
            checked={p.auto_process} onChange={(v) => setP({ ...p, auto_process: v })} />
        </div>
      </div>
    </Card>
  );
}

function SecurityCard({ system }: { system: System | null }) {
  const audit = useApi<{ at: number; actor: string; action: string; target: string | null }[]>("/v1/audit");
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Shield size={14} aria-hidden /> Security</span>}
        hint="Access is managed in Cloudflare (Zero Trust → Access) and the AUTHORIZED_EMAILS variable" />
      <div className="space-y-4 p-5">
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          {([["Cloudflare Access", system?.access_configured], ["Password on every visit", system?.password_required]] as const).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between rounded-lg border border-line px-3 py-2">
              <span className="text-fg-2">{k}</span>{v ? <Badge tone="good">On</Badge> : <Badge tone="critical">Off</Badge>}
            </div>
          ))}
        </div>
        {system?.password_in_dashboard && <PasswordForm />}
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Authorized emails</p>
          <ul className="space-y-1.5 text-sm">
            {system?.authorized_emails.map((e) => <li key={e} className="flex items-center justify-between"><span>{e}</span><Badge tone="neutral" noIcon>Cloudflare config</Badge></li>)}
          </ul>
        </div>
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Audit log</p>
          <ul className="max-h-56 space-y-1.5 overflow-auto text-xs">
            {audit.data?.slice(0, 40).map((a, i) => (
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

function PasswordForm() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ tone: "good" | "critical"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ token: string }>("/v1/settings/password", { method: "POST", body: { current: cur, password: next } });
      setUnlockToken(r.token);
      setCur("");
      setNext("");
      setMsg({ tone: "good", text: "Password changed. Every other open session has been locked." });
    } catch (x) {
      setMsg({ tone: "critical", text: x instanceof Error ? x.message : String(x) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-2 rounded-xl border border-line p-3">
      <p className="text-xs font-medium uppercase tracking-wider text-muted">Change dashboard password</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <input type="password" autoComplete="current-password" required placeholder="Current password" aria-label="Current password"
          className={inputClass} value={cur} onChange={(e) => setCur(e.target.value)} />
        <input type="password" autoComplete="new-password" required minLength={12} placeholder="New password (12+ characters)" aria-label="New password"
          className={inputClass} value={next} onChange={(e) => setNext(e.target.value)} />
      </div>
      {msg && <p role="status" className={msg.tone === "good" ? "text-xs text-[#5fd35f]" : "text-xs text-[#f08a8a]"}>{msg.text}</p>}
      <Button type="submit" loading={busy}>Change password</Button>
    </form>
  );
}

function SchedulerCard({ system }: { system: System | null }) {
  const live = useApi<{ cron: { alive: boolean; last_run: number | null; report: Record<string, unknown> | null } }>("/v1/live/status", { refreshMs: 30000 });
  const events = useApi<Activity[]>("/v1/events", { refreshMs: 30000 });
  const problems = events.data?.filter((e) => e.level !== "INFO").slice(0, 8) ?? [];
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Timer size={14} aria-hidden /> Scheduler & system</span>} hint={system?.plan} />
      <div className="space-y-4 p-5">
        <div className="flex items-center gap-2 text-sm">
          <Badge tone={live.data?.cron.alive ? "good" : "warn"}>{live.data?.cron.alive ? "Running every minute" : "Not running yet"}</Badge>
          <span className="text-xs text-muted">last run {ago(live.data?.cron.last_run)}</span>
        </div>
        {system?.config_problems.length ? (
          <Notice tone="critical" title="Configuration incomplete"><ul className="list-disc pl-4">{system.config_problems.map((p) => <li key={p}>{p}</li>)}</ul></Notice>
        ) : null}
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs">
          {system && ([["Twitch app", system.twitch_app_configured], ["TikTok app", system.tiktok_app_configured], ["Workers AI (subtitles)", system.workers_ai]] as const).map(([k, v]) => (
            <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd className="text-fg-2">{v ? "configured" : "not configured"}</dd></div>
          ))}
          {system && <><dt className="text-muted">Twitch redirect URI</dt><dd className="truncate text-fg-2"><code>{system.twitch_redirect_uri}</code></dd>
            <dt className="text-muted">TikTok redirect URI</dt><dd className="truncate text-fg-2"><code>{system.tiktok_redirect_uri}</code></dd></>}
        </dl>
        <div>
          <p className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-muted"><Cpu size={12} aria-hidden /> Recent warnings</p>
          <ul className="max-h-48 space-y-1.5 overflow-auto text-xs">
            {problems.map((e, i) => <li key={i} className="flex gap-2"><span className="w-16 shrink-0 text-muted">{ago(e.at)}</span><span className="text-fg-2">{e.message}</span></li>)}
            {events.data && !problems.length && <li className="text-muted">No warnings or errors.</li>}
          </ul>
        </div>
      </div>
    </Card>
  );
}
