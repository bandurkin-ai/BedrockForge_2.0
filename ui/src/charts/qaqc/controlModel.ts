/**
 * The model of the control chart of a standard (ТЗ 8.4.2; ADR 0020 §4, §11): the points of a group (CRM × element
 * × lab) by date or by their order in the series (DATE, SEQ, row of the file — as the core sorted them), the
 * lines μ, μ ± k₂σ, μ ± k₃σ and the actual mean, the moving average (10) with gaps where it is undefined, the
 * colours and the titles of statuses with their rules (two in a row, a run, a possible swap), the batch shown, the
 * point under the pointer, the CSV of the series as answered, the facts of a point for its card. The arrays are
 * typed (100 000 points are drawn ≤ 33 ms); a point without a date is not drawn on the date axis but counted.
 * Values are float64; nothing missing becomes 0. Pure (tested in node).
 */
import type { StandardGroup, StandardSeries } from "../../modules/qaqc/api";
import { RULE_RUN, RULE_SWAP } from "../../modules/qaqc/api";
import { dateMs, dateText, num, QC_STATUS_COLORS, statusIndex, statusLabel } from "../../modules/qaqc/format";
import { csvText, type CsvCell } from "../core/csv";
import { formatNumber, type Domain, type Scale, type Tick } from "../core/axisScale";
import { rgba32 } from "../core/palette";

export type ControlAxis = "date" | "order";

export interface ControlLine {
  key: "mu" | "warn_hi" | "warn_lo" | "fail_hi" | "fail_lo" | "mean";
  y: number;
  label: string;
}

export interface ControlModel {
  axis: ControlAxis;
  /** points drawn */
  n: number;
  /** date (ms, UTC) or number in the series (1…) of each point drawn */
  xs: Float64Array;
  ys: Float64Array;
  /** index of each point drawn in the series */
  idx: Int32Array;
  status: Uint8Array;
  /** the moving average at the points drawn; NaN — undefined (the line breaks) */
  ma: Float64Array;
  /** points of the series without a date (not drawn on the date axis) */
  noDate: number;
  /** points drawn by status: norm, warning, failure, not assessed (−1 of the core) */
  byStatus: [number, number, number, number];
  lines: ControlLine[];
  xDomain: Domain;
  yDomain: Domain;
}

const finite = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);
const sigmaLabel = (k: number) => formatNumber(k, 3);

/** The lines of the group: μ and its bands when the CRM is in the reference, the actual mean of the group. */
export function controlLines(group: Pick<StandardGroup, "mu" | "sd" | "mean">, kWarn: number, kFail: number): ControlLine[] {
  const out: ControlLine[] = [];
  if (finite(group.mu) && finite(group.sd)) {
    const { mu, sd } = group;
    out.push(
      { key: "fail_hi", y: mu + kFail * sd, label: `+${sigmaLabel(kFail)}σ` },
      { key: "warn_hi", y: mu + kWarn * sd, label: `+${sigmaLabel(kWarn)}σ` },
      { key: "mu", y: mu, label: "μ" },
      { key: "warn_lo", y: mu - kWarn * sd, label: `−${sigmaLabel(kWarn)}σ` },
      { key: "fail_lo", y: mu - kFail * sd, label: `−${sigmaLabel(kFail)}σ` },
    );
  }
  if (finite(group.mean)) out.push({ key: "mean", y: group.mean, label: "x̄" });
  return out;
}

function pad(min: number, max: number, share = 0.04): Domain {
  if (!(max > min)) {
    const d = Math.abs(min) * 0.1 || 1;
    return { min: min - d, max: max + d };
  }
  const d = (max - min) * share;
  return { min: min - d, max: max + d };
}

/** The points of the series on the axis, its lines and domains; the y domain holds the points and every line. */
export function controlModel(series: StandardSeries, group: Pick<StandardGroup, "mu" | "sd" | "mean">, kWarn: number, kFail: number, axis: ControlAxis): ControlModel {
  const total = series.x.length;
  const xs = new Float64Array(total);
  const ys = new Float64Array(total);
  const idx = new Int32Array(total);
  const status = new Uint8Array(total);
  const ma = new Float64Array(total);
  const byStatus: [number, number, number, number] = [0, 0, 0, 0];
  let n = 0;
  let noDate = 0;
  let xmin = Infinity;
  let xmax = -Infinity;
  let ymin = Infinity;
  let ymax = -Infinity;
  for (let i = 0; i < total; i++) {
    const y = series.x[i];
    if (!finite(y)) continue;
    let x: number;
    if (axis === "order") x = i + 1;
    else {
      const t = dateMs(series.date[i]);
      if (t === null) {
        noDate++;
        continue;
      }
      x = t;
    }
    const st = statusIndex(series.status[i]);
    xs[n] = x;
    ys[n] = y;
    idx[n] = i;
    status[n] = st;
    const m = series.ma[i];
    ma[n] = finite(m) ? m : Number.NaN;
    byStatus[st]++;
    if (x < xmin) xmin = x;
    if (x > xmax) xmax = x;
    if (y < ymin) ymin = y;
    if (y > ymax) ymax = y;
    n++;
  }
  const lines = controlLines(group, kWarn, kFail);
  for (const l of lines) {
    if (l.y < ymin) ymin = l.y;
    if (l.y > ymax) ymax = l.y;
  }
  if (ymin === Infinity) [ymin, ymax] = [0, 1];
  if (xmin === Infinity) [xmin, xmax] = axis === "order" ? [1, 2] : [0, 86_400_000];
  const xDomain = axis === "order" ? { min: xmin - 0.5, max: Math.max(xmax, xmin + 1) + 0.5 } : pad(xmin, xmax, 0.02);
  return { axis, n, xs: xs.subarray(0, n), ys: ys.subarray(0, n), idx: idx.subarray(0, n), status: status.subarray(0, n), ma: ma.subarray(0, n), noDate, byStatus, lines, xDomain, yDomain: pad(ymin, ymax) };
}

/** The status of a point of the series in words, with its rules (Т8.4-1). */
export const pointLabel = (series: StandardSeries, i: number): string => statusLabel(series.status[i], series.rule[i] ?? 0);

export const STATUS_PIXELS = QC_STATUS_COLORS.map((c) => rgba32(c, 0.9));

/** One pixel colour per point drawn (the canvas layer). */
export function statusPixels(model: ControlModel): Uint32Array {
  const out = new Uint32Array(model.n);
  for (let j = 0; j < model.n; j++) out[j] = STATUS_PIXELS[model.status[j]];
  return out;
}

/** Points drawn of the batch shown (the group is of one lab: the batch is enough). */
export function batchPoints(model: ControlModel, series: StandardSeries, batch: string | null | undefined): number[] {
  if (!batch) return [];
  const out: number[] = [];
  for (let j = 0; j < model.n; j++) if (series.batch[model.idx[j]] === batch) out.push(j);
  return out;
}

/** Points drawn with a mark of a run or of a possible swap (drawn over the points, at most `max`). */
export function markedPoints(model: ControlModel, series: StandardSeries, max = 5000): { run: number[]; swap: number[] } {
  const run: number[] = [];
  const swap: number[] = [];
  for (let j = 0; j < model.n && run.length + swap.length < max; j++) {
    const r = series.rule[model.idx[j]] ?? 0;
    if (r & RULE_SWAP) swap.push(j);
    else if (r & RULE_RUN) run.push(j);
  }
  return { run, swap };
}

/** The point drawn nearest to (px, py) of the viewBox within `radius` px, or −1. */
export function nearestPoint(model: ControlModel, sx: Scale, sy: Scale, px: number, py: number, radius = 8): number {
  let best = -1;
  let bestD = radius * radius;
  for (let j = 0; j < model.n; j++) {
    const x = sx.map(model.xs[j]);
    const y = sy.map(model.ys[j]);
    if (x === null || y === null) continue;
    const d = (x - px) ** 2 + (y - py) ** 2;
    if (d <= bestD) {
      bestD = d;
      best = j;
    }
  }
  return best;
}

/**
 * The SVG path of the moving average over the points drawn: a gap where it is undefined; points closer than half
 * a pixel along x to the previous one kept are skipped (the line of 100 000 points stays a few thousand segments).
 */
export function maPath(model: ControlModel, sx: Scale, sy: Scale): string {
  const parts: string[] = [];
  let pen = false;
  let lastX = -Infinity;
  for (let j = 0; j < model.n; j++) {
    const v = model.ma[j];
    if (!Number.isFinite(v)) {
      pen = false;
      continue;
    }
    const x = sx.map(model.xs[j]);
    const y = sy.map(v);
    if (x === null || y === null) {
      pen = false;
      continue;
    }
    if (pen && x - lastX < 0.5 && j + 1 < model.n && Number.isFinite(model.ma[j + 1])) continue;
    parts.push(`${pen ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`);
    pen = true;
    lastX = x;
  }
  return parts.join("");
}

const DAY = 86_400_000;
const two = (n: number) => String(n).padStart(2, "0");

/** Ticks of a date axis (UTC): days or weeks for a short span, then months (1, 2, 3, 6) and years. */
export function dateTicks(domain: Domain, count = 7): Tick[] {
  const span = domain.max - domain.min;
  if (!(span > 0)) return [];
  const out: Tick[] = [];
  const days = span / DAY;
  if (days <= count * 14) {
    const step = [1, 2, 7, 14].find((d) => days / d <= count) ?? 14;
    const start = Math.ceil(domain.min / DAY) * DAY;
    for (let t = start; t <= domain.max && out.length < 60; t += step * DAY) {
      const d = new Date(t);
      out.push({ value: t, label: `${two(d.getUTCDate())}.${two(d.getUTCMonth() + 1)}` });
    }
    return out;
  }
  const months = days / 30.44;
  const step = [1, 2, 3, 6, 12, 24, 60].find((m) => months / m <= count) ?? 120;
  const first = new Date(domain.min);
  // months since year 0, aligned to the step: quarters start in January, April…; years — in January
  for (let k = Math.ceil((first.getUTCFullYear() * 12 + first.getUTCMonth()) / step) * step; out.length < 60; k += step) {
    const y = Math.floor(k / 12);
    const m = k % 12;
    const t = Date.UTC(y, m, 1);
    if (t > domain.max) break;
    if (t >= domain.min) out.push({ value: t, label: step >= 12 ? String(y) : `${two(m + 1)}.${y}` });
  }
  return out;
}

const swapText = (v: string | string[] | null | undefined): string | null => (Array.isArray(v) ? v.join(", ") : v || null);

export const CSV_HEADER = ["row", "date", "seq", "batch", "x", "z", "status", "rule", "ma", "swap"] as const;

/** The CSV of the series: every point as the core answered (a point without a date too), in the order of the series. */
export function controlCsv(series: StandardSeries): string {
  const rows: CsvCell[][] = [];
  for (let i = 0; i < series.x.length; i++) {
    rows.push([series.row[i], series.date[i], series.seq[i], series.batch[i], series.x[i], series.z[i], series.status[i], series.rule[i], series.ma[i], swapText(series.swap[i])]);
  }
  return csvText(CSV_HEADER, rows);
}

/** The facts of a point of the series for its card, in Russian. */
export function pointFacts(series: StandardSeries, i: number, unit: string | null): [string, string][] {
  const u = unit ? ` ${unit}` : "";
  const facts: [string, string][] = [
    ["Дата", dateText(dateMs(series.date[i]))],
    ["Номер в партии (SEQ)", num(series.seq[i])],
    ["Партия", series.batch[i] ?? "—"],
    ["Значение", `${num(series.x[i], 6)}${u}`],
    ["z", num(series.z[i], 3)],
    ["Статус", pointLabel(series, i)],
    ["Скользящее среднее (10)", `${num(series.ma[i], 6)}${finite(series.ma[i]) ? u : ""}`],
  ];
  const swap = swapText(series.swap[i]);
  if (swap) facts.push(["Возможна подмена", swap]);
  return facts;
}
