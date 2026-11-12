"use client";

/**
 * Hand-written SVG charts following the dataviz method:
 * - colours = validated categorical slots (series-1..3) on the #111113 chart surface; status colours only
 *   for state, always with a shape + text label;
 * - bars <= 24px, 4px rounded data-end, square at the baseline, 2px surface gap between adjacent bars;
 * - 2px lines; hairline solid gridlines; one y-axis only;
 * - hover tooltip on every mark (hit targets larger than the mark), crosshair on line charts;
 * - a legend whenever there are >= 2 series; a table view for every chart.
 */

import { Table2 } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { cx } from "@/components/ui";

export const SERIES = ["var(--color-series-1)", "var(--color-series-2)", "var(--color-series-3)"];
const SURFACE = "#111113";

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function compact(n: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

/* ------------------------------------------------------------------------------------ frame */
export function ChartFrame({ title, legend, children, table, summary }: {
  title: string; legend?: { label: string; color: string; shape?: "dot" | "ring" | "x" }[];
  children: ReactNode; table: { columns: string[]; rows: (string | number)[][] }; summary?: string;
}) {
  const [showTable, setShowTable] = useState(false);
  return (
    <figure className="m-0" aria-label={title}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-4">
          {legend?.map((l) => (
            <span key={l.label} className="inline-flex items-center gap-1.5 text-xs text-fg-2">
              <LegendMark color={l.color} shape={l.shape ?? "dot"} />
              {l.label}
            </span>
          ))}
        </div>
        <button
          onClick={() => setShowTable((s) => !s)}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted hover:bg-card-2 hover:text-fg-2"
          aria-pressed={showTable}
        >
          <Table2 size={13} aria-hidden /> {showTable ? "Chart" : "Table"}
        </button>
      </div>
      {showTable ? (
        <div className="max-h-72 overflow-auto rounded-lg border border-line">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-card-2 text-muted">
              <tr>{table.columns.map((c) => <th key={c} className="px-3 py-2 font-medium">{c}</th>)}</tr>
            </thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i} className="border-t border-line text-fg-2">
                  {r.map((c, j) => <td key={j} className="px-3 py-1.5 tabular-nums">{c}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        children
      )}
      {summary && <figcaption className="sr-only">{summary}</figcaption>}
    </figure>
  );
}

function LegendMark({ color, shape }: { color: string; shape: "dot" | "ring" | "x" }) {
  return (
    <svg width="12" height="12" aria-hidden>
      {shape === "dot" && <circle cx="6" cy="6" r="4.5" fill={color} />}
      {shape === "ring" && <circle cx="6" cy="6" r="3.75" fill="none" stroke={color} strokeWidth="2" />}
      {shape === "x" && <path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke={color} strokeWidth="2" strokeLinecap="round" />}
    </svg>
  );
}

function Tooltip({ x, y, width, children }: { x: number; y: number; width: number; children: ReactNode }) {
  const left = Math.min(Math.max(8, x + 12), width - 188);
  return (
    <div
      className="pointer-events-none absolute z-10 w-44 rounded-lg border border-line-strong bg-[#18181b]/95 px-3 py-2 text-xs shadow-xl backdrop-blur"
      style={{ left, top: Math.max(0, y - 10) }}
      role="tooltip"
    >
      {children}
    </div>
  );
}

/** Rounded data-end, square baseline (vertical bar). */
function barPath(x: number, y: number, w: number, h: number, r = 4) {
  if (h <= 0) return "";
  const rr = Math.min(r, h, w / 2);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
}

/* ------------------------------------------------------------------------------------ grouped bars */
export function GroupedBars({ title, data, series, height = 220 }: {
  title: string; data: { label: string; values: number[] }[]; series: string[]; height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 36, r: 8, t: 8, b: 26 };
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const rawMax = Math.max(1, ...data.flatMap((d) => d.values));
  // counts are whole numbers: small ranges get integer ticks only
  const max = rawMax <= 4 ? 4 : niceMax(rawMax);
  const band = data.length ? iw / data.length : iw;
  const n = series.length;
  const barW = Math.min(24, Math.max(3, (band * 0.7 - (n - 1) * 2) / n));
  const groupW = n * barW + (n - 1) * 2;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const every = Math.ceil(data.length / Math.max(1, Math.floor(iw / 56)));

  return (
    <ChartFrame
      title={title}
      legend={series.length > 1 ? series.map((s, i) => ({ label: s, color: SERIES[i] })) : undefined}
      table={{ columns: ["Date", ...series], rows: data.map((d) => [d.label, ...d.values]) }}
      summary={`${title}: ${data.length} days`}
    >
      <div ref={ref} className="relative">
        <svg width={width} height={height} role="img" aria-label={title}>
          {ticks.map((t) => {
            const y = pad.t + ih - (t / max) * ih;
            return (
              <g key={t}>
                <line x1={pad.l} x2={width - pad.r} y1={y} y2={y} stroke={t === 0 ? "var(--color-axis)" : "var(--color-grid)"} />
                <text x={pad.l - 8} y={y + 3.5} textAnchor="end" className="fill-[#8d8d97] text-[10px]">{compact(t)}</text>
              </g>
            );
          })}
          {data.map((d, i) => {
            const gx = pad.l + i * band + (band - groupW) / 2;
            return (
              <g key={d.label}>
                {hover === i && <rect x={pad.l + i * band} y={pad.t} width={band} height={ih} fill="rgb(255 255 255 / 0.03)" />}
                {d.values.map((v, j) => {
                  const h = (v / max) * ih;
                  return <path key={j} d={barPath(gx + j * (barW + 2), pad.t + ih - h, barW, h)} fill={SERIES[j]} />;
                })}
                {i % every === 0 && (
                  <text x={pad.l + i * band + band / 2} y={height - 8} textAnchor="middle" className="fill-[#8d8d97] text-[10px]">
                    {d.label.slice(5)}
                  </text>
                )}
                <rect
                  x={pad.l + i * band} y={pad.t} width={band} height={ih} fill="transparent"
                  onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                />
              </g>
            );
          })}
        </svg>
        {hover !== null && data[hover] && (
          <Tooltip x={pad.l + hover * band + band / 2} y={pad.t} width={width}>
            <p className="mb-1 font-medium text-fg">{data[hover].label}</p>
            {series.map((s, j) => (
              <p key={s} className="flex justify-between gap-3 text-fg-2">
                <span className="inline-flex items-center gap-1.5"><LegendMark color={SERIES[j]} shape="dot" />{s}</span>
                <span className="tabular-nums text-fg">{data[hover].values[j]}</span>
              </p>
            ))}
          </Tooltip>
        )}
      </div>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------------------------ lines */
export function LineChart({ title, data, series, height = 220 }: {
  title: string; data: { label: string; values: number[] }[]; series: string[]; height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 40, r: 12, t: 10, b: 26 };
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const max = niceMax(Math.max(1, ...data.flatMap((d) => d.values)));
  const x = (i: number) => pad.l + (data.length <= 1 ? iw / 2 : (i / (data.length - 1)) * iw);
  const y = (v: number) => pad.t + ih - (v / max) * ih;
  const every = Math.ceil(data.length / Math.max(1, Math.floor(iw / 64)));

  return (
    <ChartFrame
      title={title}
      legend={series.length > 1 ? series.map((s, i) => ({ label: s, color: SERIES[i] })) : undefined}
      table={{ columns: ["Date", ...series], rows: data.map((d) => [d.label, ...d.values]) }}
    >
      <div ref={ref} className="relative">
        <svg
          width={width} height={height} role="img" aria-label={title}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
            const i = Math.round(((e.clientX - r.left - pad.l) / Math.max(1, iw)) * (data.length - 1));
            setHover(data.length ? Math.max(0, Math.min(data.length - 1, i)) : null);
          }}
          onMouseLeave={() => setHover(null)}
        >
          {[0, 0.5, 1].map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={width - pad.r} y1={y(t * max)} y2={y(t * max)} stroke={t === 0 ? "var(--color-axis)" : "var(--color-grid)"} />
              <text x={pad.l - 8} y={y(t * max) + 3.5} textAnchor="end" className="fill-[#8d8d97] text-[10px]">{compact(t * max)}</text>
            </g>
          ))}
          {data.map((d, i) =>
            i % every === 0 ? (
              <text key={d.label} x={x(i)} y={height - 8} textAnchor="middle" className="fill-[#8d8d97] text-[10px]">{d.label.slice(5)}</text>
            ) : null,
          )}
          {series.map((_, j) => (
            <path
              key={j}
              d={data.map((d, i) => `${i ? "L" : "M"}${x(i)},${y(d.values[j])}`).join(" ")}
              fill="none" stroke={SERIES[j]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
            />
          ))}
          {hover !== null && (
            <g>
              <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} stroke="var(--color-axis)" />
              {series.map((_, j) => (
                <circle key={j} cx={x(hover)} cy={y(data[hover].values[j])} r={4.5} fill={SERIES[j]} stroke={SURFACE} strokeWidth={2} />
              ))}
            </g>
          )}
        </svg>
        {hover !== null && data[hover] && (
          <Tooltip x={x(hover)} y={pad.t} width={width}>
            <p className="mb-1 font-medium text-fg">{data[hover].label}</p>
            {series.map((s, j) => (
              <p key={s} className="flex justify-between gap-3 text-fg-2">
                <span className="inline-flex items-center gap-1.5"><LegendMark color={SERIES[j]} shape="dot" />{s}</span>
                <span className="tabular-nums text-fg">{data[hover].values[j].toLocaleString()}</span>
              </p>
            ))}
          </Tooltip>
        )}
      </div>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------------------------ highlight timeline */
export type TimelinePoint = { id: string; t: number; score: number; status: string; label: string; category: string };
const STATUS_STYLE: Record<string, { color: string; shape: "dot" | "ring" | "x"; label: string }> = {
  clipped: { color: "var(--color-good)", shape: "dot", label: "Clipped" },
  accepted: { color: "var(--color-good)", shape: "dot", label: "Accepted" },
  provisional: { color: "var(--color-warn)", shape: "ring", label: "Needs verification" },
  rejected: { color: "#8d8d97", shape: "x", label: "Rejected" },
  duplicate: { color: "#8d8d97", shape: "x", label: "Duplicate" },
  selected: { color: "var(--color-good)", shape: "dot", label: "Selected" },
  discovered: { color: "#8d8d97", shape: "ring", label: "Not selected" },
};

export function HighlightTimeline({ points, threshold, onSelect, selected, title = "Detected moments by quality score over the stream", metric = "quality" }: {
  points: TimelinePoint[]; threshold?: number; onSelect?: (id: string) => void; selected?: string | null; title?: string; metric?: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<TimelinePoint | null>(null);
  const height = 200;
  const pad = { l: 34, r: 12, t: 10, b: 26 };
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const tmax = Math.max(60, ...points.map((p) => p.t));
  const x = (t: number) => pad.l + (t / tmax) * iw;
  const y = (s: number) => pad.t + ih - s * ih;
  const used = useMemo(() => [...new Set(points.map((p) => STATUS_STYLE[p.status]?.label ?? p.status))], [points]);
  const legend = Object.values(STATUS_STYLE).filter((s, i, arr) => used.includes(s.label) && arr.findIndex((o) => o.label === s.label) === i);
  const hm = (t: number) => `${Math.floor(t / 3600)}:${String(Math.floor((t % 3600) / 60)).padStart(2, "0")}`;

  return (
    <ChartFrame
      title={title}
      legend={legend.map((l) => ({ label: l.label, color: l.color, shape: l.shape }))}
      table={{ columns: ["Stream time", metric, "Status", "Category", "Title"],
               rows: points.map((p) => [hm(p.t), Math.round(p.score * 100), p.status, p.category, p.label]) }}
    >
      <div ref={ref} className="relative">
        <svg width={width} height={height} role="img" aria-label="Highlight timeline">
          {[0, 0.5, 1].map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--color-axis)" : "var(--color-grid)"} />
              <text x={pad.l - 8} y={y(t) + 3.5} textAnchor="end" className="fill-[#8d8d97] text-[10px]">{t * 100}</text>
            </g>
          ))}
          {threshold !== undefined && (
            <g>
              <line x1={pad.l} x2={width - pad.r} y1={y(threshold)} y2={y(threshold)} stroke="#c4c4cc" strokeOpacity={0.35} />
              <text x={width - pad.r} y={y(threshold) - 4} textAnchor="end" className="fill-[#c4c4cc] text-[10px]">
                threshold {Math.round(threshold * 100)}
              </text>
            </g>
          )}
          {[0, 0.25, 0.5, 0.75, 1].map((f) => (
            <text key={f} x={x(f * tmax)} y={height - 8} textAnchor="middle" className="fill-[#8d8d97] text-[10px]">{hm(f * tmax)}</text>
          ))}
          {points.map((p) => {
            const st = STATUS_STYLE[p.status] ?? STATUS_STYLE.rejected;
            const cx = x(p.t);
            const cy = y(p.score);
            const sel = selected === p.id;
            return (
              <g key={p.id} onMouseEnter={() => setHover(p)} onMouseLeave={() => setHover(null)}
                 onClick={() => onSelect?.(p.id)} className={onSelect ? "cursor-pointer" : undefined}>
                <circle cx={cx} cy={cy} r={12} fill="transparent" />
                {sel && <circle cx={cx} cy={cy} r={9} fill="none" stroke="#c4b5fd" strokeWidth={1.5} />}
                {st.shape === "dot" && <circle cx={cx} cy={cy} r={5} fill={st.color} stroke={SURFACE} strokeWidth={2} />}
                {st.shape === "ring" && <circle cx={cx} cy={cy} r={4.5} fill={SURFACE} stroke={st.color} strokeWidth={2} />}
                {st.shape === "x" && <path d={`M${cx - 4},${cy - 4}l8,8M${cx + 4},${cy - 4}l-8,8`} stroke={st.color} strokeWidth={2} strokeLinecap="round" />}
              </g>
            );
          })}
        </svg>
        {hover && (
          <Tooltip x={x(hover.t)} y={y(hover.score)} width={width}>
            <p className="font-medium text-fg">{hover.label}</p>
            <p className="mt-0.5 text-fg-2">{hm(hover.t)} · {metric} {Math.round(hover.score * 100)}</p>
            <p className="text-muted">{STATUS_STYLE[hover.status]?.label ?? hover.status}</p>
          </Tooltip>
        )}
      </div>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------------------------ horizontal bars */
export function HBarList({ title, rows, max: maxIn, format = (v) => String(Math.round(v * 100)), note }: {
  title: string; rows: { label: string; value: number | null; weight?: string; tag?: ReactNode }[];
  max?: number; format?: (v: number) => string; note?: ReactNode;
}) {
  const max = maxIn ?? Math.max(1e-9, ...rows.map((r) => r.value ?? 0));
  return (
    <ChartFrame title={title} table={{ columns: ["Item", "Value", "Weight"],
      rows: rows.map((r) => [r.label, r.value === null ? "n/a" : format(r.value), r.weight ?? ""]) }}>
      <ul className="space-y-2.5" aria-label={title}>
        {rows.map((r) => (
          <li key={r.label} className="grid grid-cols-[minmax(0,9rem)_1fr_3rem] items-center gap-3 text-xs">
            <span className="truncate text-fg-2" title={r.label}>
              {r.label}
              {r.weight && <span className="ml-1 text-muted">{r.weight}</span>}
            </span>
            <span className="relative h-2.5 rounded-r bg-transparent">
              {r.value === null ? (
                <span className="text-[10px] text-muted">signal unavailable</span>
              ) : (
                <span
                  className="absolute inset-y-0 left-0 rounded-r-[4px] bg-[#3987e5]"
                  style={{ width: `${Math.max(1.5, ((r.value ?? 0) / max) * 100)}%` }}
                />
              )}
            </span>
            <span className={cx("text-right tabular-nums", r.value === null ? "text-muted" : "text-fg")}>
              {r.value === null ? "—" : format(r.value)}
              {r.tag}
            </span>
          </li>
        ))}
      </ul>
      {note && <p className="mt-3 text-xs text-muted">{note}</p>}
    </ChartFrame>
  );
}
