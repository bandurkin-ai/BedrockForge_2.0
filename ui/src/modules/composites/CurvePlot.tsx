/**
 * A simple curve in SVG for the windows «Урезка» and «Декластеризация» (ТЗ 8.2.2; ADR 0018 §7, decision 8: the
 * full chart of 8.3 is Э13): one line y(x) on linear axes (y may be logarithmic), nice ticks, a vertical mark
 * (the chosen P), a horizontal reference (the mean «до», the threshold), a marked point (the best size). A missing
 * value breaks the line — never drawn as zero; on a log axis a value ≤ 0 is left out as well. With `onPick` a click
 * or ←/→ picks the nearest x of the data — the window decides what it means. No dependencies; brand tokens only.
 */
import type { KeyboardEvent, MouseEvent } from "react";
import { niceTicks } from "../../charts/column/scale";
import own from "./CurvePlot.module.css";

export type Num = number | null | undefined;

export interface Tick {
  value: number;
  label: string;
}

const ok = (v: Num, log: boolean): v is number => typeof v === "number" && Number.isFinite(v) && (!log || v > 0);

/** [lo, hi] of the drawable values; a single value is widened; none — null. */
export function extent(values: readonly Num[], log = false): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (!ok(v, log)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (lo > hi) return null;
  if (lo === hi) return log ? [lo / 2, hi * 2] : lo === 0 ? [-1, 1] : [lo - Math.abs(lo) * 0.1, hi + Math.abs(hi) * 0.1];
  return [lo, hi];
}

/** value → pixel on [a; b]; logarithmic — by log10. */
export function scale(lo: number, hi: number, a: number, b: number, log = false): (v: number) => number {
  const f = log ? Math.log10 : (v: number) => v;
  const l = f(lo);
  const k = (b - a) / (f(hi) - l || 1);
  return (v) => a + (f(v) - l) * k;
}

/** Ticks of a log axis: 1, 2, 5 × 10ⁿ inside [lo; hi] (only 10ⁿ when there are many decades). */
export function logTicks(lo: number, hi: number): number[] {
  if (!(lo > 0) || !(hi > lo)) return [];
  const out: number[] = [];
  const many = Math.log10(hi / lo) > 3;
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e += 1)
    for (const m of many ? [1] : [1, 2, 5]) {
      const v = Number((m * 10 ** e).toPrecision(12));
      if (v >= lo * (1 - 1e-9) && v <= hi * (1 + 1e-9)) out.push(v);
    }
  return out;
}

/** The paths of a line: a missing x or y (or ≤ 0 on a log axis) ends a piece; a lone point is a dot. */
export function segments(xs: readonly Num[], ys: readonly Num[], sx: (v: number) => number, sy: (v: number) => number, logY = false): { lines: string[]; dots: [number, number][] } {
  const lines: string[] = [];
  const dots: [number, number][] = [];
  let piece: [number, number][] = [];
  const flush = () => {
    if (piece.length === 1) dots.push(piece[0]);
    else if (piece.length > 1) lines.push(piece.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(""));
    piece = [];
  };
  const n = Math.min(xs.length, ys.length);
  for (let i = 0; i < n; i += 1) {
    const x = xs[i];
    const y = ys[i];
    if (ok(x, false) && ok(y, logY)) piece.push([sx(x), sy(y)]);
    else flush();
  }
  flush();
  return { lines, dots };
}

/** The index of the drawable x nearest to `x`; none — -1. */
export function nearestIndex(xs: readonly Num[], ys: readonly Num[], x: number): number {
  let best = -1;
  for (let i = 0; i < xs.length; i += 1) {
    const v = xs[i];
    if (!ok(v, false) || !ok(ys[i], false)) continue;
    if (best < 0 || Math.abs(v - x) < Math.abs((xs[best] as number) - x)) best = i;
  }
  return best;
}

/** «12,5», «0,001», «1,2·10⁶». */
export function tickText(v: number): string {
  if (v !== 0 && (Math.abs(v) >= 1e6 || Math.abs(v) < 1e-3)) {
    const e = Math.floor(Math.log10(Math.abs(v)));
    const sup = String(e).replace(/[-0-9]/g, (c) => "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"["-0123456789".indexOf(c)]);
    return `${Number((v / 10 ** e).toPrecision(3)).toLocaleString("ru-RU")}·10${sup}`;
  }
  return v.toLocaleString("ru-RU", { maximumFractionDigits: 4 });
}

export interface CurvePlotProps {
  title: string;
  xs: readonly Num[];
  ys: readonly Num[];
  xLabel: string;
  yLabel?: string;
  logY?: boolean;
  /** Ticks of x given by the window (the probability axis); otherwise nice ticks. */
  xTicks?: readonly Tick[];
  /** A vertical mark (the chosen P). */
  markX?: number | null;
  /** A horizontal reference line with its label. */
  refY?: { value: number; label: string } | null;
  /** A marked point (the best size). */
  point?: { x: number; y: number; label: string } | null;
  onPick?: (x: number) => void;
  width?: number;
  height?: number;
  testId?: string;
}

const PAD = { left: 52, right: 10, top: 8, bottom: 30 };

export function CurvePlot({ title, xs, ys, xLabel, yLabel, logY = false, xTicks, markX = null, refY = null, point = null, onPick, width = 300, height = 170, testId }: CurvePlotProps) {
  const xr = extent(xs);
  const yr = extent([...ys, refY?.value, point?.y], logY);
  const box = { x0: PAD.left, x1: width - PAD.right, y0: PAD.top, y1: height - PAD.bottom };
  const empty = !xr || !yr;
  const sx = xr ? scale(xr[0], xr[1], box.x0, box.x1) : () => 0;
  const sy = yr ? scale(yr[0], yr[1], box.y1, box.y0, logY) : () => 0;
  const xt: Tick[] = xr ? (xTicks ? xTicks.filter((t) => t.value >= xr[0] - 1e-9 && t.value <= xr[1] + 1e-9) : niceTicks(xr[0], xr[1], 5).map((v) => ({ value: v, label: tickText(v) }))) : [];
  const yt = yr ? (logY ? logTicks(yr[0], yr[1]) : niceTicks(yr[0], yr[1], 4)) : [];
  const { lines, dots } = empty ? { lines: [], dots: [] } : segments(xs, ys, sx, sy, logY);
  const pick = (x: number) => {
    const i = nearestIndex(xs, ys, x);
    if (i >= 0 && onPick) onPick(xs[i] as number);
  };
  const onClick = (e: MouseEvent<SVGSVGElement>) => {
    if (!onPick || !xr) return;
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / (r.width || 1)) * width;
    pick(xr[0] + ((px - box.x0) / (box.x1 - box.x0)) * (xr[1] - xr[0]));
  };
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    if (!onPick || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    e.preventDefault();
    const order = xs.map((x, i) => ({ x, i })).filter((p) => ok(p.x, false) && ok(ys[p.i], false)).sort((a, b) => (a.x as number) - (b.x as number));
    if (!order.length) return;
    const cur = markX === null ? -1 : order.findIndex((p) => Math.abs((p.x as number) - markX) < 1e-9 * Math.max(1, Math.abs(markX)));
    const next = cur < 0 ? (e.key === "ArrowLeft" ? order.length - 1 : 0) : Math.max(0, Math.min(order.length - 1, cur + (e.key === "ArrowLeft" ? -1 : 1)));
    onPick(order[next].x as number);
  };
  return (
    <figure className={own.plot} data-testid={testId}>
      <figcaption className={own.title}>{title}</figcaption>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className={onPick ? `${own.svg} ${own.pickable}` : own.svg}
        role="img"
        aria-label={`${title}: ${xLabel}${yLabel ? ` — ${yLabel}` : ""}${onPick ? "; щелчок или ←/→ — выбрать" : ""}`}
        tabIndex={onPick ? 0 : undefined}
        onClick={onPick ? onClick : undefined}
        onKeyDown={onPick ? onKey : undefined}
      >
        <rect className={own.frame} x={box.x0} y={box.y0} width={box.x1 - box.x0} height={box.y1 - box.y0} />
        {empty ? (
          <text className={own.empty} x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
            Нет данных
          </text>
        ) : (
          <>
            {yt.map((v) => (
              <g key={`y${v}`}>
                <line className={own.grid} x1={box.x0} x2={box.x1} y1={sy(v)} y2={sy(v)} />
                <text className={own.tick} x={box.x0 - 4} y={sy(v) + 3} textAnchor="end">
                  {tickText(v)}
                </text>
              </g>
            ))}
            {xt.map((t) => (
              <g key={`x${t.value}`}>
                <line className={own.grid} x1={sx(t.value)} x2={sx(t.value)} y1={box.y0} y2={box.y1} />
                <text className={own.tick} x={sx(t.value)} y={box.y1 + 12} textAnchor="middle">
                  {t.label}
                </text>
              </g>
            ))}
            {refY && ok(refY.value, logY) && (
              <g data-testid="curve-ref">
                <line className={own.ref} x1={box.x0} x2={box.x1} y1={sy(refY.value)} y2={sy(refY.value)} />
                <text className={own.refText} x={box.x1 - 2} y={sy(refY.value) - 3} textAnchor="end">
                  {refY.label}
                </text>
              </g>
            )}
            {lines.map((d, i) => (
              <path key={i} className={own.line} d={d} />
            ))}
            {dots.map(([x, y], i) => (
              <circle key={`d${i}`} className={own.dot} cx={x} cy={y} r={2} />
            ))}
            {markX !== null && xr && markX >= xr[0] - 1e-9 && markX <= xr[1] + 1e-9 && <line className={own.mark} data-testid="curve-mark" x1={sx(markX)} x2={sx(markX)} y1={box.y0} y2={box.y1} />}
            {point && ok(point.y, logY) && (
              <g data-testid="curve-point">
                <circle className={own.point} cx={sx(point.x)} cy={sy(point.y)} r={4} />
                <text className={own.refText} x={sx(point.x) + 6} y={sy(point.y) - 6}>
                  {point.label}
                </text>
              </g>
            )}
          </>
        )}
        <text className={own.axis} x={(box.x0 + box.x1) / 2} y={height - 4} textAnchor="middle">
          {xLabel}
        </text>
      </svg>
      {yLabel && <div className={own.yLabel}>{yLabel}</div>}
    </figure>
  );
}
