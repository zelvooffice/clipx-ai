"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  BarChart3, Clapperboard, Flame, LayoutDashboard, Lock, Menu, Radio, Scissors, Send, Settings, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";
import { cx } from "@/components/ui";
import { api, LOCK_EVENT, setUnlockToken, unlockToken, useApi } from "@/lib/api";
import { ProcessorProvider, useProcessor } from "@/components/Processor";
import { LockScreen } from "./LockScreen";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/live", label: "Live Monitor", icon: Radio },
  { href: "/clips", label: "Auto Clips", icon: Scissors },
  { href: "/viral", label: "Viral Edits", icon: Flame },
  { href: "/editor", label: "Video Editor", icon: Clapperboard },
  { href: "/publishing", label: "Publishing", icon: Send },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/settings", label: "Settings", icon: Settings },
];

type LiveStatus = { live: { title: string | null } | null; target: string; cron: { alive: boolean; last_run: number | null } };

function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2.5 px-2" aria-label="CLIPX AI home">
      <span className="accent-gradient grid size-8 place-items-center rounded-lg text-sm font-black text-white shadow-[0_6px_20px_-8px_rgba(139,92,246,0.9)]">
        X
      </span>
      <span className="text-[15px] font-semibold tracking-tight">
        CLIPX <span className="text-gradient">AI</span>
      </span>
    </Link>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  return (
    <nav className="mt-6 space-y-0.5" aria-label="Main">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cx(
              "relative flex h-9 items-center gap-3 rounded-lg px-3 text-sm transition-colors",
              active ? "text-fg" : "text-muted hover:bg-card hover:text-fg-2",
            )}
          >
            {active && (
              <motion.span layoutId="nav-active" className="absolute inset-0 rounded-lg border border-line bg-card"
                transition={{ type: "spring", stiffness: 500, damping: 40 }} />
            )}
            {active && <span className="accent-gradient absolute left-0 top-2 h-5 w-0.5 rounded-full" />}
            <Icon size={16} className="relative" aria-hidden />
            <span className="relative">{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

function LivePill() {
  const { data } = useApi<LiveStatus>("/v1/live/status", { refreshMs: 15000 });
  if (!data) return <span className="h-7 w-24 rounded-full skeleton" />;
  if (data.live)
    return (
      <Link href="/live" className="inline-flex h-7 items-center gap-2 rounded-full border border-[#ef4444]/40 bg-[#ef4444]/10 px-3 text-xs font-semibold text-[#fca5a5]">
        <span className="live-dot size-2 rounded-full bg-[#ef4444]" aria-hidden /> LIVE
        <span className="hidden max-w-[16rem] truncate font-normal text-fg-2 md:inline">{data.live.title}</span>
      </Link>
    );
  return (
    <span className="inline-flex h-7 items-center gap-2 rounded-full border border-line-strong bg-card px-3 text-xs text-muted">
      <span className="size-2 rounded-full bg-[#55555c]" aria-hidden /> Offline
      {!data.cron.alive && <span className="hidden text-[#fbc94a] md:inline">· scheduler not running yet</span>}
    </span>
  );
}

const STAGE: Record<string, string> = { "loading engine": "Loading editor", downloading: "Downloading", "analysing audio": "Reading audio",
  transcribing: "Subtitles", rendering: "Rendering", uploading: "Uploading", done: "Done" };

/** Shows what this tab is editing; the free plan renders video in the browser. */
function ProcessorPill() {
  const p = useProcessor();
  if (p.current) {
    const r = p.current.progress.ratio;
    return (
      <Link href="/clips" className="inline-flex h-7 max-w-[18rem] items-center gap-2 rounded-full border border-[#8b5cf6]/40 bg-[#8b5cf6]/10 px-3 text-xs text-fg-2"
        title={`Editing "${p.current.title}" in this tab — keep it open`} role="status">
        <span className="size-2 animate-pulse rounded-full bg-[#a78bfa]" aria-hidden />
        <span className="truncate">{STAGE[p.current.progress.stage]}{r !== undefined && p.current.progress.stage === "rendering" ? ` ${Math.round(r * 100)}%` : ""}</span>
        <span className="hidden truncate text-muted md:inline">· {p.current.title}</span>
      </Link>
    );
  }
  if (!p.enabled) return null;
  return (
    <button onClick={() => p.setPaused(!p.paused)} className="hidden h-7 items-center gap-2 rounded-full border border-line-strong bg-card px-3 text-xs text-muted hover:text-fg-2 sm:inline-flex"
      title={p.paused ? "Automatic editing is paused in this tab" : "This tab edits new clips automatically while it is open"}>
      <span className={cx("size-2 rounded-full", p.paused ? "bg-[#55555c]" : "bg-[#34d399]")} aria-hidden />
      {p.paused ? "Editing paused" : p.lastError ? "Editor: last clip failed" : "Editor ready"}
    </button>
  );
}

type Gate = "checking" | "locked" | "open";

export function Shell({ children }: { children: ReactNode }) {
  const [gate, setGate] = useState<Gate>("checking");
  const [email, setEmail] = useState<string | null>(null);
  const [setup, setSetup] = useState<number | null>(null);
  const [needsCode, setNeedsCode] = useState(false);

  useEffect(() => {
    api<{ unlocked: boolean; email: string; setup_required: boolean; setup_needs_code: boolean; min_length: number }>("/v1/session")
      .then((s) => { setEmail(s.email); setNeedsCode(s.setup_needs_code); setSetup(s.setup_required ? s.min_length : null); setGate(s.unlocked ? "open" : "locked"); })
      .catch(() => setGate(unlockToken() ? "open" : "locked"));
    const onLocked = () => setGate("locked");
    window.addEventListener(LOCK_EVENT, onLocked);
    return () => window.removeEventListener(LOCK_EVENT, onLocked);
  }, []);

  async function lock() {
    try {
      await api("/v1/session/lock", { method: "POST", body: {} });
    } finally {
      setUnlockToken(null);
      setGate("locked");
    }
  }

  if (gate === "checking") return <div className="min-h-screen bg-ink" aria-busy="true" />;
  if (gate === "locked") return <LockScreen email={email} setup={setup !== null} needsCode={needsCode} minLength={setup ?? 12}
    onUnlocked={() => { setSetup(null); setGate("open"); }} />;
  return <ProcessorProvider><App email={email} onLock={lock}>{children}</App></ProcessorProvider>;
}

function App({ children, email, onLock }: { children: ReactNode; email: string | null; onLock: () => void }) {
  const [open, setOpen] = useState(false);
  const path = usePathname();
  useEffect(() => setOpen(false), [path]);

  return (
    <div className="app-glow min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-3 focus:z-[60] focus:rounded-lg focus:bg-card-2 focus:px-3 focus:py-2 focus:text-sm">
        Skip to content
      </a>
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r border-line bg-panel/80 px-3 py-5 backdrop-blur lg:block">
        <Logo />
        <NavLinks />
        <div className="absolute inset-x-3 bottom-4 rounded-xl border border-line bg-card p-3 text-xs text-muted">
          <p className="font-medium text-fg-2">Monitoring</p>
          <p className="mt-0.5">twitch.tv/lacy</p>
        </div>
      </aside>

      <AnimatePresence>
        {open && (
          <>
            <motion.div className="fixed inset-0 z-40 bg-black/60 lg:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              exit={{ opacity: 0 }} onClick={() => setOpen(false)} />
            <motion.aside className="fixed inset-y-0 left-0 z-50 w-64 border-r border-line bg-panel px-3 py-5 lg:hidden"
              initial={{ x: -280 }} animate={{ x: 0 }} exit={{ x: -280 }} transition={{ type: "spring", damping: 30, stiffness: 300 }}>
              <div className="flex items-center justify-between">
                <Logo />
                <button className="rounded-lg p-2 text-muted hover:text-fg" onClick={() => setOpen(false)} aria-label="Close menu">
                  <X size={18} />
                </button>
              </div>
              <NavLinks onNavigate={() => setOpen(false)} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b border-line bg-ink/70 px-4 backdrop-blur-xl sm:px-6">
          <div className="flex items-center gap-3">
            <button className="rounded-lg p-2 text-muted hover:text-fg lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu">
              <Menu size={18} />
            </button>
            <LivePill />
            <ProcessorPill />
          </div>
          <div className="flex items-center gap-3 text-xs text-muted">
            {email && email !== "owner" && (
              <span className="flex items-center gap-2">
                <span className="grid size-7 place-items-center rounded-full border border-line-strong bg-card text-[11px] font-semibold uppercase text-fg-2">
                  {email[0]}
                </span>
                <span className="hidden sm:inline">{email}</span>
              </span>
            )}
            <button onClick={onLock} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line-strong px-2.5 text-xs text-fg-2 hover:bg-card-2 hover:text-fg" aria-label="Lock dashboard">
              <Lock size={13} aria-hidden /> <span className="hidden sm:inline">Lock</span>
            </button>
          </div>
        </header>
        <main id="main" tabIndex={-1} className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-10">{children}</main>
      </div>
    </div>
  );
}
