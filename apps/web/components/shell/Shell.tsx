"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  BarChart3, Clapperboard, Flame, LayoutDashboard, Menu, Radio, Scissors, Send, Settings, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";
import { cx } from "@/components/ui";
import { useApi } from "@/lib/api";

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

type LiveStatus = { live: { title: string | null; analysis_mode: string } | null; monitor: { alive: boolean } };

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
      {!data.monitor.alive && <span className="hidden text-[#fbc94a] md:inline">· monitor not running</span>}
    </span>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  const path = usePathname();
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    fetch("/api/session").then((r) => (r.ok ? r.json() : null)).then((d) => setEmail(d?.email ?? null)).catch(() => null);
  }, []);

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
          </div>
          <div className="flex items-center gap-3 text-xs text-muted">
            {email && (
              <span className="flex items-center gap-2">
                <span className="grid size-7 place-items-center rounded-full border border-line-strong bg-card text-[11px] font-semibold uppercase text-fg-2">
                  {email[0]}
                </span>
                <span className="hidden sm:inline">{email}</span>
              </span>
            )}
          </div>
        </header>
        <main id="main" tabIndex={-1} className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-10">{children}</main>
      </div>
    </div>
  );
}
