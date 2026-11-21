/**
 * The model of the charts of blanks (ТЗ 8.4.2; ADR 0020 §4, §11): the blanks of a laboratory by date or by their
 * order in the series (DATE, SEQ, row of the file — as the core sorted them) with the line k_б·DL, and the
 * carry-over scatter «предыдущая проба партии — бланк» (pairs where both have a value). Statuses as the core gives
 * them: 0 — норма, 2 — отказ, −1 (or none) — «не оценён» (no DL) with a colour of its own, never the norm. A point
 * without a date is not drawn on the date axis but counted; nothing missing becomes 0. The arrays are typed.
 * Also: the batch shown, the point under the pointer, the CSV of the series as answered, the facts for the card.
 * Pure (tested in node).
 */
import type { BlankGroup, BlanksAnswer } from "../../modules/qaqc/api";
import { dateMs, dateText, NOT_ASSESSED, num, QC_STATUS_COLORS, QC_STATUS_TITLES, statusIndex } from "../../modules/qaqc/format";
import { formatNumber, type Domain, type Scale } from "../core/axisScale";
import { csvText, type CsvCell } from "../core/csv";
import { rgba32 } from "../core/palette";
import type { ControlAxis } from "./controlModel";

export type BlankSeries = NonNullable<BlanksAnswer["series"]>;

/** 0 — норма, 1 — предупреждение, 2 — отказ, 3 — не оценён (no DL): the statuses of `format.ts`. */
export type BlankStatus = 0 | 1 | 2 | 3;
export { NOT_ASSESSED };
export const BLANK_STATUS_TITLES = QC_STATUS_TITLES;
/** «Не оценён» — Slate Grey of the brand: neither the norm (Cobalt) nor a failure. */
export const BLANK_STATUS_COLORS = QC_STATUS_COLORS;
export const BLANK_STATUS_PIXELS = BLANK_STATUS_COLORS.map((c) => rgba32(c, 0.9));

/** The status of the core (−1 or none — not assessed) as an index of the titles and colours. */
export const blankStatus = (s: number | null | undefined): BlankStatus => statusIndex(s);

export const blankStatusTitle = (s: number | null | undefined): string => BLANK_STATUS_TITLES[blankStatus(s)];

/** Points drawn: x and y, the index in the series, the status. */
export interface BlankPoints {
  n: number;
  xs: Float64Array;
  ys: Float64Array;
  idx: Int32Array;
  status: Uint8Array;
  /** points drawn by status: norm, warning, failure, not assessed */
  byStatus: [number, number, number, number];
  xDomain: Domain;
  yDomain: Domain;
}

export interface BlankLine {
  key: "limit";
  y: number;
  label: string;
}

export interface BlankModel extends BlankPoints {
  axis: ControlAxis;
  /** points of the series without a date (not drawn on the date axis) */
  noDate: number;
  lines: BlankLine[];
}

const finite = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);

function pad(min: number, max: number, share = 0.04): Domain {
  if (!(max > min)) {
    const d = Math.abs(min) * 0.1 || 1;
    return { min: min - d, max: max + d };
  }
  const d = (max - min) * share;
  return { min: min - d, max: max + d };
}

/** The line of the threshold k_б·DL of the laboratory; none when its blanks have no DL or different ones. */
export function blankLines(group: Pick<BlankGroup, "limit">, kBlank: number | null | undefined): BlankLine[] {
  if (!finite(group.limit)) return [];
  return [{ key: "limit", y: group.limit, label: finite(kBlank) ? `${formatNumber(kBlank, 3)}·DL` : "k·DL" }];
}

interface Acc {
  xs: Float64Array;
  ys: Float64Array;
  idx: Int32Array;
  status: Uint8Array;
  byStatus: [number, number, number, number];
  n: number;
  xmin: number;
  xmax: number;
  ymin: number;
  ymax: number;
}

const acc = (total: number): Acc => ({ xs: new Float64Array(total), ys: new Float64Array(total), idx: new Int32Array(total), status: new Uint8Array(total), byStatus: [0, 0, 0, 0], n: 0, xmin: Infinity, xmax: -Infinity, ymin: Infinity, ymax: -Infinity });

function push(a: Acc, x: number, y: number, i: number, st: BlankStatus): void {
  a.xs[a.n] = x;
  a.ys[a.n] = y;
  a.idx[a.n] = i;
  a.status[a.n] = st;
  a.byStatus[st]++;
  if (x < a.xmin) a.xmin = x;
  if (x > a.xmax) a.xmax = x;
  if (y < a.ymin) a.ymin = y;
  if (y > a.ymax) a.ymax = y;
  a.n++;
}

const done = (a: Acc, xDomain: Domain, yDomain: Domain): BlankPoints => ({ n: a.n, xs: a.xs.subarray(0, a.n), ys: a.ys.subarray(0, a.n), idx: a.idx.subarray(0, a.n), status: a.status.subarray(0, a.n), byStatus: a.byStatus, xDomain, yDomain });

/** The blanks of a laboratory on the axis with the line k_б·DL; the y domain holds the points, the line and 0. */
export function blankModel(series: BlankSeries, group: Pick<BlankGroup, "limit">, kBlank: number | null | undefined, axis: ControlAxis): BlankModel {
  const total = series.x.length;
  const a = acc(total);
  let noDate = 0;
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
    push(a, x, y, i, blankStatus(series.status[i]));
  }
  const lines = blankLines(group, kBlank);
  let { xmin, xmax, ymin, ymax } = a;
  for (const l of lines) {
    if (l.y < ymin) ymin = l.y;
    if (l.y > ymax) ymax = l.y;
  }
  if (ymin === Infinity) [ymin, ymax] = [0, 1];
  // blanks are near zero: the axis starts at 0 when nothing is below it
  if (ymin > 0) ymin = 0;
  if (xmin === Infinity) [xmin, xmax] = axis === "order" ? [1, 2] : [0, 86_400_000];
  const xDomain = axis === "order" ? { min: xmin - 0.5, max: Math.max(xmax, xmin + 1) + 0.5 } : pad(xmin, xmax, 0.02);
  return { ...done(a, xDomain, pad(ymin, ymax)), axis, noDate, lines };
}

/** The carry-over: the pairs (the previous sample of the batch, the blank) where both have a value. */
export function carryModel(series: BlankSeries): BlankPoints {
  const total = series.x.length;
  const a = acc(total);
  for (let i = 0; i < total; i++) {
    const y = series.x[i];
    const x = series.prev[i];
    if (finite(x) && finite(y)) push(a, x, y, i, blankStatus(series.status[i]));
  }
  let { xmin, xmax, ymin, ymax } = a;
  if (xmin === Infinity) [xmin, xmax, ymin, ymax] = [0, 1, 0, 1];
  if (xmin > 0) xmin = 0;
  if (ymin > 0) ymin = 0;
  return done(a, pad(xmin, xmax), pad(ymin, ymax));
}

/** «r = 0,912, пар 48»; r undefined (fewer than three pairs, no spread) — «r не определён». */
export function carryTitle(carry: BlanksAnswer["carryover"] | undefined, pairs: number): string {
  const n = carry?.n ?? pairs;
  const r = carry?.pearson;
  return `${finite(r) ? `r = ${formatNumber(r, 3)}` : "r не определён"}, пар ${n.toLocaleString("ru-RU")}`;
}

/** One pixel colour per point drawn (the canvas layer). */
export function blankPixels(p: BlankPoints): Uint32Array {
  const out = new Uint32Array(p.n);
  for (let j = 0; j < p.n; j++) out[j] = BLANK_STATUS_PIXELS[p.status[j]];
  return out;
}

/** Points drawn of the batch shown (the series is of one lab: the batch is enough). */
export function blankBatchPoints(p: BlankPoints, series: BlankSeries, batch: string | null | undefined): number[] {
  if (!batch) return [];
  const out: number[] = [];
  for (let j = 0; j < p.n; j++) if (series.batch[p.idx[j]] === batch) out.push(j);
  return out;
}

/** The point drawn nearest to (px, py) of the viewBox within `radius` px, or −1. */
export function nearestBlank(p: BlankPoints, sx: Scale, sy: Scale, px: number, py: number, radius = 8): number {
  let best = -1;
  let bestD = radius * radius;
  for (let j = 0; j < p.n; j++) {
    const x = sx.map(p.xs[j]);
    const y = sy.map(p.ys[j]);
    if (x === null || y === null) continue;
    const d = (x - px) ** 2 + (y - py) ** 2;
    if (d <= bestD) {
      bestD = d;
      best = j;
    }
  }
  return best;
}

export const BLANK_CSV_HEADER = ["row", "date", "seq", "batch", "x", "status", "prev"] as const;

/** The CSV of the series: every blank as the core answered (one without a date too), in the order of the series. */
export function blankCsv(series: BlankSeries): string {
  const rows: CsvCell[][] = [];
  for (let i = 0; i < series.x.length; i++) rows.push([series.row[i], series.date[i], series.seq[i], series.batch[i], series.x[i], series.status[i], series.prev[i]]);
  return csvText(BLANK_CSV_HEADER, rows);
}

export const CARRY_CSV_HEADER = ["row", "batch", "prev", "x", "status"] as const;

/** The CSV of the carry-over: the pairs drawn (both values given), in the order of the series. */
export function carryCsv(series: BlankSeries, carry: BlankPoints): string {
  const rows: CsvCell[][] = [];
  for (let j = 0; j < carry.n; j++) {
    const i = carry.idx[j];
    rows.push([series.row[i], series.batch[i], series.prev[i], series.x[i], series.status[i]]);
  }
  return csvText(CARRY_CSV_HEADER, rows);
}

/** The facts of a blank for its card, in Russian. */
export function blankFacts(series: BlankSeries, i: number, unit: string | null, group: Pick<BlankGroup, "dl" | "limit"> | null): [string, string][] {
  const u = (v: number | null | undefined) => (finite(v) && unit ? ` ${unit}` : "");
  // the threshold of the sample itself (its own DL), else that of its laboratory
  const own = series.limit?.[i];
  const limit = finite(own) ? own : group?.limit;
  return [
    ["Дата", dateText(dateMs(series.date[i]))],
    ["Номер в партии (SEQ)", num(series.seq[i])],
    ["Партия", series.batch[i] ?? "—"],
    ["Значение", `${num(series.x[i], 6)}${u(series.x[i])}`],
    ["Предыдущая проба партии", `${num(series.prev[i], 6)}${u(series.prev[i])}`],
    ["DL", `${num(group?.dl, 6)}${u(group?.dl)}`],
    ["Порог k_б·DL", `${num(limit, 6)}${u(limit)}`],
    ["Статус", blankStatusTitle(series.status[i])],
  ];
}
