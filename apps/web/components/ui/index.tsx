"use client";

import { motion } from "framer-motion";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Info,
  Loader2,
  type LucideIcon,
  OctagonX,
  RefreshCw,
} from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

/* ----------------------------------------------------------------------------- layout */
export function PageHeader({ title, subtitle, actions, icon: Icon }: {
  title: string; subtitle?: ReactNode; actions?: ReactNode; icon?: LucideIcon;
}) {
  return (
    <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <div className="flex items-center gap-3">
          {Icon && (
            <span className="grid size-9 place-items-center rounded-xl border border-line bg-card text-fg-2">
              <Icon size={18} aria-hidden />
            </span>
          )}
          <h1 className="text-2xl font-semibold tracking-tight text-fg">{title}</h1>
        </div>
        {subtitle && <p className="mt-2 max-w-2xl text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ children, className, interactive, as: As = "section" }: {
  children: ReactNode; className?: string; interactive?: boolean; as?: "section" | "div" | "article";
}) {
  const M = motion[As];
  return (
    <M
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className={cx(
        "rounded-2xl border border-line bg-card shadow-[0_1px_0_0_rgba(255,255,255,0.03)_inset]",
        interactive && "transition-colors hover:border-line-strong hover:bg-card-2",
        className,
      )}
    >
      {children}
    </M>
  );
}

export function CardHeader({ title, hint, actions }: { title: ReactNode; hint?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
      <div className="min-w-0">
        <h2 className="text-sm font-medium text-fg">{title}</h2>
        {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ----------------------------------------------------------------------------- controls */
type Variant = "primary" | "secondary" | "ghost" | "danger";
export function Button({ variant = "secondary", loading, icon: Icon, children, className, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean; icon?: LucideIcon }) {
  const styles: Record<Variant, string> = {
    primary: "accent-gradient text-white shadow-[0_8px_24px_-12px_rgba(139,92,246,0.8)] hover:brightness-110",
    secondary: "border border-line-strong bg-card-2 text-fg hover:bg-[#1e1e22]",
    ghost: "text-fg-2 hover:bg-card-2 hover:text-fg",
    danger: "border border-[#d03b3b]/40 bg-[#d03b3b]/10 text-[#fca5a5] hover:bg-[#d03b3b]/20",
  };
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cx(
        "inline-flex h-9 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium transition",
        "disabled:cursor-not-allowed disabled:opacity-50",
        styles[variant],
        className,
      )}
    >
      {loading ? <Loader2 size={15} className="animate-spin" aria-hidden /> : Icon ? <Icon size={15} aria-hidden /> : null}
      {children}
    </button>
  );
}

export function Toggle({ checked, onChange, label, disabled, description }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean; description?: ReactNode;
}) {
  return (
    <label className={cx("flex items-start justify-between gap-6", disabled && "opacity-50")}>
      <span className="min-w-0">
        <span className="block text-sm text-fg">{label}</span>
        {description && <span className="mt-0.5 block text-xs text-muted">{description}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx(
          "relative h-6 w-11 shrink-0 rounded-full border transition-colors",
          checked ? "accent-gradient border-transparent" : "border-line-strong bg-card-2",
        )}
      >
        <motion.span
          layout
          transition={{ type: "spring", stiffness: 500, damping: 32 }}
          className={cx("absolute top-0.5 size-[18px] rounded-full bg-white shadow", checked ? "right-0.5" : "left-0.5")}
        />
      </button>
    </label>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-fg-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

export const linkButton =
  "inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-line-strong bg-card-2 px-3.5 text-sm " +
  "font-medium text-fg transition hover:bg-[#1e1e22]";

export const inputClass =
  "h-9 w-full rounded-lg border border-line-strong bg-[#0b0b0d] px-3 text-sm text-fg placeholder:text-muted " +
  "focus:border-[#8b5cf6]/60 focus:outline-none";

/* ----------------------------------------------------------------------------- status */
export type Tone = "good" | "warn" | "serious" | "critical" | "neutral" | "info" | "accent";
const TONES: Record<Tone, { cls: string; icon: LucideIcon }> = {
  good: { cls: "border-[#0ca30c]/35 bg-[#0ca30c]/10 text-[#5fd35f]", icon: CheckCircle2 },
  warn: { cls: "border-[#fab219]/35 bg-[#fab219]/10 text-[#fbc94a]", icon: AlertTriangle },
  serious: { cls: "border-[#ec835a]/35 bg-[#ec835a]/10 text-[#f2a283]", icon: AlertTriangle },
  critical: { cls: "border-[#d03b3b]/40 bg-[#d03b3b]/10 text-[#f08a8a]", icon: OctagonX },
  neutral: { cls: "border-line-strong bg-card-2 text-fg-2", icon: CircleDashed },
  info: { cls: "border-[#3b82f6]/35 bg-[#3b82f6]/10 text-[#93c5fd]", icon: Info },
  accent: { cls: "border-[#8b5cf6]/35 bg-[#8b5cf6]/10 text-[#c4b5fd]", icon: Info },
};

/** Status is never colour-only: every badge carries an icon and a text label. */
export function Badge({ tone = "neutral", children, icon, noIcon }: {
  tone?: Tone; children: ReactNode; icon?: LucideIcon; noIcon?: boolean;
}) {
  const t = TONES[tone];
  const Icon = icon ?? t.icon;
  return (
    <span className={cx("inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium", t.cls)}>
      {!noIcon && <Icon size={12} aria-hidden />}
      {children}
    </span>
  );
}

export function Notice({ tone = "info", title, children, action }: {
  tone?: Tone; title: ReactNode; children?: ReactNode; action?: ReactNode;
}) {
  const t = TONES[tone];
  const Icon = t.icon;
  return (
    <div className={cx("flex gap-3 rounded-xl border p-4", t.cls.replace(/text-\S+/, ""))} role="status">
      <Icon size={18} className={cx("mt-0.5 shrink-0", t.cls.match(/text-\S+/)?.[0])} aria-hidden />
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium text-fg">{title}</p>
        {children && <div className="mt-1 text-fg-2">{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}

/* ----------------------------------------------------------------------------- data display */
export function Stat({ label, value, sub, icon: Icon, loading }: {
  label: string; value: ReactNode; sub?: ReactNode; icon?: LucideIcon; loading?: boolean;
}) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wider text-muted">{label}</span>
        {Icon && <Icon size={16} className="text-muted" aria-hidden />}
      </div>
      <div className="mt-3 text-3xl font-semibold tracking-tight text-fg tabular-nums">
        {loading ? <Skeleton className="h-8 w-20" /> : value}
      </div>
      {sub && <div className="mt-1.5 text-xs text-muted">{sub}</div>}
    </Card>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton rounded-md", className)} aria-hidden />;
}

export function Empty({ icon: Icon = CircleDashed, title, children, action }: {
  icon?: LucideIcon; title: string; children?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <span className="mb-4 grid size-12 place-items-center rounded-2xl border border-line bg-card-2 text-muted">
        <Icon size={20} aria-hidden />
      </span>
      <p className="text-sm font-medium text-fg">{title}</p>
      {children && <div className="mt-1.5 max-w-md text-sm text-muted">{children}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: { message: string; status?: number }; onRetry?: () => void }) {
  return (
    <Notice
      tone="critical"
      title={error.status === 503 ? "Backend not reachable or not configured" : "Couldn’t load this data"}
      action={onRetry && <Button variant="ghost" icon={RefreshCw} onClick={onRetry}>Retry</Button>}
    >
      {error.message}
    </Notice>
  );
}

export function Meter({ value, label }: { value: number | null | undefined; label: string }) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  return (
    <div className="flex items-center gap-2" aria-label={`${label}: ${value === null || value === undefined ? "n/a" : Math.round(v * 100)}`}>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-card-2">
        <div className="h-full rounded-full bg-[#3987e5]" style={{ width: `${v * 100}%` }} />
      </div>
      <span className="w-7 text-right text-xs tabular-nums text-fg-2">
        {value === null || value === undefined ? "—" : Math.round(v * 100)}
      </span>
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string; count?: number }[];
}) {
  return (
    <div role="tablist" className="inline-flex rounded-lg border border-line bg-card p-1">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            "relative h-7 rounded-md px-3 text-xs font-medium transition-colors",
            value === o.value ? "text-fg" : "text-muted hover:text-fg-2",
          )}
        >
          {value === o.value && (
            <motion.span layoutId={`tab-${options.map((x) => x.value).join("")}`} className="absolute inset-0 rounded-md bg-card-2" />
          )}
          <span className="relative">
            {o.label}
            {o.count !== undefined && <span className="ml-1.5 text-muted">{o.count}</span>}
          </span>
        </button>
      ))}
    </div>
  );
}
