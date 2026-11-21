/**
 * The models of the charts of pairs (ТЗ 8.4.2; ADR 0020 §4, §11) — duplicates and external control: the scatter
 * x1 — x2 with the lines 1:1, y = (1 ± 0,1)·x, y = (1 ± 0,2)·x and RMA (a square plot: both axes in one range), the
 * points coloured by HARD against the threshold of the type; the ranked HARD (HARD ascending against 100·i / n) with
 * the threshold of the type, the share of the criterion and the share reached; the Thompson–Howard chart (points of
 * the groups m̄ — median |x1 − x2|, the line a + b·m, the table P(c)); the excluded pairs in words; the CSV of each
 * chart as answered. The arrays are typed (100 000 pairs are drawn ≤ 33 ms). A threshold compares as the core does:
 * a value on the boundary is on the soft side. Values are float64; nothing missing becomes 0. Pure (tested in node).
 */
import type { Excluded, PairPoints, ThompsonHoward } from "../../modules/qaqc/api";
import { int, num, pct, STATUS_COLORS } from "../../modules/qaqc/format";
import { domainOf, formatNumber, type Domain, type Scale } from "../core/axisScale";
import { csvText, type CsvCell } from "../core/csv";
import type { Pt } from "../core/lasso";
import { rgba32 } from "../core/palette";

const finite = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);

/** `a ≤ b` as the core compares it (ADR 0020 §4): ≤ b + 1e−9·max(1, |b|) — the boundary is on the soft side. */
export const atMost = (a: number, b: number): boolean => a <= b + 1e-9 * Math.max(1, Math.abs(b));

/** The segment of y = a + b·x inside the square `d` × `d`; null — no line or it misses the plot. */
export function lineSegment(a: number | null | undefined, b: number | null | undefined, d: Domain): [Pt, Pt] | null {
  if (!finite(a) || !finite(b)) return null;
  let lo = d.min;
  let hi = d.max;
  if (b !== 0) {
    const xa = (d.min - a) / b;
    const xb = (d.max - a) / b;
    lo = Math.max(lo, Math.min(xa, xb));
    hi = Math.min(hi, Math.max(xa, xb));
  } else if (a < d.min || a > d.max) return null;
  if (!(hi > lo)) return null;
  return [
    { x: lo, y: a + b * lo },
    { x: hi, y: a + b * hi },
  ];
}

export type PairLineKey = "one" | "p10" | "m10" | "p20" | "m20" | "rma";

export interface PairLine {
  key: PairLineKey;
  a: number;
  b: number;
  label: string;
  seg: [Pt, Pt];
}

export interface PairModel {
  /** points drawn */
  n: number;
  xs: Float64Array;
  ys: Float64Array;
  /** index of each point drawn in the answer */
  idx: Int32Array;
  /** 1 — HARD above the threshold of the type */
  over: Uint8Array;
  /** points drawn: HARD within the threshold, above it */
  byClass: [number, number];
  /** one range of both axes (a square plot) */
  domain: Domain;
  lines: PairLine[];
}

/** The lines of the scatter: 1:1, ±10 %, ±20 % and RMA (when the core gave it), those that cross the plot. */
export function pairLines(domain: Domain, rma: { a: number | null; b: number | null } | null | undefined): PairLine[] {
  const want: [PairLineKey, number | null | undefined, number | null | undefined, string][] = [
    ["p20", 0, 1.2, "+20 %"],
    ["p10", 0, 1.1, "+10 %"],
    ["one", 0, 1, "1:1"],
    ["m10", 0, 0.9, "−10 %"],
    ["m20", 0, 0.8, "−20 %"],
    ["rma", rma?.a, rma?.b, "RMA"],
  ];
  const out: PairLine[] = [];
  for (const [key, a, b, label] of want) {
    const seg = lineSegment(a, b, domain);
    if (seg && finite(a) && finite(b)) out.push({ key, a, b, label, seg });
  }
  return out;
}

/** The points of the pairs (x1 — the original or the main lab, x2 — the duplicate or the check) and the lines. */
export function pairModel(points: PairPoints, limit: number, rma?: { a: number | null; b: number | null } | null): PairModel {
  const total = Math.min(points.x1.length, points.x2.length);
  const xs = new Float64Array(total);
  const ys = new Float64Array(total);
  const idx = new Int32Array(total);
  const over = new Uint8Array(total);
  const byClass: [number, number] = [0, 0];
  let n = 0;
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < total; i++) {
    const x = points.x1[i];
    const y = points.x2[i];
    if (!finite(x) || !finite(y)) continue;
    const h = points.hard?.[i];
    const o = finite(h) && !atMost(h, limit) ? 1 : 0;
    xs[n] = x;
    ys[n] = y;
    idx[n] = i;
    over[n] = o;
    byClass[o]++;
    if (x < lo) lo = x;
    if (y < lo) lo = y;
    if (x > hi) hi = x;
    if (y > hi) hi = y;
    n++;
  }
  // non-negative values: the square starts at 0 (the rays ±10 %, ±20 % fan out of the origin)
  const domain = (lo === Infinity ? null : domainOf([lo >= 0 ? 0 : lo, hi], "linear", 0.04, lo >= 0 ? 0 : undefined)) ?? { min: 0, max: 1 };
  return { n, xs: xs.subarray(0, n), ys: ys.subarray(0, n), idx: idx.subarray(0, n), over: over.subarray(0, n), byClass, domain, lines: pairLines(domain, rma) };
}

/** The order of drawing: the points within the threshold first, those above it over them (never hidden by the norm). */
export function drawOrder(model: Pick<PairModel, "n" | "over">): { low: Int32Array; high: Int32Array; all: Int32Array } {
  const all = new Int32Array(model.n);
  let k = 0;
  for (let j = 0; j < model.n; j++) if (!model.over[j]) all[k++] = j;
  const split = k;
  for (let j = 0; j < model.n; j++) if (model.over[j]) all[k++] = j;
  return { low: all.subarray(0, split), high: all.subarray(split), all };
}

/** Within the threshold — Cobalt, above it — the colour of a failure. */
export const PAIR_COLORS = [STATUS_COLORS[0], STATUS_COLORS[2]] as const;
export const PAIR_PIXELS = PAIR_COLORS.map((c) => rgba32(c, 0.85));

/** One pixel colour per point drawn (the canvas layer). */
export function pairPixels(model: Pick<PairModel, "n" | "over">): Uint32Array {
  const out = new Uint32Array(model.n);
  for (let j = 0; j < model.n; j++) out[j] = PAIR_PIXELS[model.over[j]];
  return out;
}

/** The point drawn nearest to (px, py) of the viewBox within `radius` px, or −1. */
export function nearestPair(model: Pick<PairModel, "n" | "xs" | "ys">, sx: Scale, sy: Scale, px: number, py: number, radius = 8): number {
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

export const PAIR_CSV_HEADER = ["row", "x1", "x2", "hard"] as const;

/** The CSV of the pairs: every point as answered, in its order. */
export function pairCsv(points: PairPoints): string {
  const rows: CsvCell[][] = [];
  for (let i = 0; i < points.x1.length; i++) rows.push([points.row[i], points.x1[i], points.x2[i], points.hard[i]]);
  return csvText(PAIR_CSV_HEADER, rows);
}

/** The facts of a pair for the card of its sample, in Russian (`titles` — of x1 and x2). */
export function pairFacts(points: PairPoints, i: number, unit: string | null, titles: [string, string]): [string, string][] {
  const u = unit ? ` ${unit}` : "";
  const x1 = points.x1[i];
  const x2 = points.x2[i];
  const s = x1 + x2;
  return [
    [titles[0], `${num(x1, 6)}${u}`],
    [titles[1], `${num(x2, 6)}${u}`],
    ["HARD", pct(points.hard[i], 2)],
    ["HRD", pct(finite(s) && s > 0 ? (100 * (x1 - x2)) / s : null, 2)],
  ];
}

/** The ranked HARD: the points as answered, the threshold of the type, the share of the criterion and the one reached. */
export interface RankedModel {
  n: number;
  /** 100·i / n, % */
  xs: Float64Array;
  /** HARD, % */
  ys: Float64Array;
  over: Uint8Array;
  limit: number;
  shareLimit: number;
  /** the share of the pairs with HARD ≤ the threshold, % (of all pairs); null — not assessed */
  shareAt: number | null;
  yDomain: Domain;
}

export function rankedModel(ranked: { p: readonly number[]; hard: readonly number[] }, limit: number, shareLimit: number, shareAt: number | null): RankedModel {
  const total = Math.min(ranked.p.length, ranked.hard.length);
  const xs = new Float64Array(total);
  const ys = new Float64Array(total);
  const over = new Uint8Array(total);
  let n = 0;
  let top = finite(limit) ? limit : 0;
  for (let i = 0; i < total; i++) {
    const p = ranked.p[i];
    const h = ranked.hard[i];
    if (!finite(p) || !finite(h)) continue;
    xs[n] = p;
    ys[n] = h;
    over[n] = atMost(h, limit) ? 0 : 1;
    if (h > top) top = h;
    n++;
  }
  return { n, xs: xs.subarray(0, n), ys: ys.subarray(0, n), over: over.subarray(0, n), limit, shareLimit, shareAt: finite(shareAt) ? shareAt : null, yDomain: { min: 0, max: top > 0 ? top * 1.08 : 1 } };
}

/** The SVG path through the ranked points (ascending), in pixels. */
export function rankedPath(model: RankedModel, sx: Scale, sy: Scale): string {
  const parts: string[] = [];
  for (let j = 0; j < model.n; j++) {
    const x = sx.map(model.xs[j]);
    const y = sy.map(model.ys[j]);
    if (x === null || y === null) continue;
    parts.push(`${parts.length ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`);
  }
  return parts.join("");
}

/** «пар 4 000, показано 2 000»: the ranked HARD comes thinned to 2000 points by the core (the shares are of all pairs). */
export const rankedNote = (used: number, shown: number): string => `пар ${int(used)}${shown < used ? `, показано ${int(shown)} (доли — по всем)` : ""}`;

export const RANKED_CSV_HEADER = ["p", "hard"] as const;

export function rankedCsv(ranked: { p: readonly number[]; hard: readonly number[] }): string {
  return csvText(
    RANKED_CSV_HEADER,
    ranked.p.map((p, i) => [p, ranked.hard[i]]),
  );
}

/** A value c of the table P(c): `q` — 10, 50, 90 for the percentiles of the means of the pairs, null — a given c. */
export interface ThPrecision {
  c: number;
  p: number | null;
  q?: number | null;
}

/**
 * The block `th` as the core answers it (core `qaqc/calc_pairs.py`, `thompson.py`): beyond ADR 0020 §10 — `need`
 * (pairs the method needs: max(50, 2·group)), `groups.n` (pairs in each group), `precision[].q`; `state` — `ok`,
 * `few_pairs` (fewer than `need` pairs), `no_fit` (every group has the same mean).
 */
export type ThBlock = Omit<ThompsonHoward, "groups" | "precision"> & { need?: number; groups: { m: number[]; med: number[]; n?: number[] }; precision: ThPrecision[] };

/** «P10 средних пар», «P50 …», «P90 …»; a given c — «задано». */
export const precisionTitle = (q: number | null | undefined): string => (typeof q === "number" && Number.isFinite(q) ? `P${q} средних пар` : "задано");

/** The Thompson–Howard chart: the points of the groups, the line med = a + b·m, the domains. */
export interface ThModel {
  n: number;
  xs: Float64Array;
  ys: Float64Array;
  /** the line over the x range; null — no a or b */
  line: [Pt, Pt] | null;
  xDomain: Domain;
  yDomain: Domain;
}

export function thModel(th: Pick<ThBlock, "a" | "b" | "groups">): ThModel {
  const m = th.groups?.m ?? [];
  const med = th.groups?.med ?? [];
  const total = Math.min(m.length, med.length);
  const xs = new Float64Array(total);
  const ys = new Float64Array(total);
  let n = 0;
  for (let i = 0; i < total; i++) {
    if (!finite(m[i]) || !finite(med[i])) continue;
    xs[n] = m[i];
    ys[n] = med[i];
    n++;
  }
  const xDomain = domainOf(xs.subarray(0, n), "linear", 0.05, 0) ?? { min: 0, max: 1 };
  let line: [Pt, Pt] | null = null;
  const ends: number[] = Array.from(ys.subarray(0, n));
  if (finite(th.a) && finite(th.b)) {
    line = [
      { x: xDomain.min, y: th.a + th.b * xDomain.min },
      { x: xDomain.max, y: th.a + th.b * xDomain.max },
    ];
    ends.push(line[0].y, line[1].y);
  }
  const yDomain = domainOf(ends, "linear", 0.06, 0) ?? { min: 0, max: 1 };
  return { n, xs: xs.subarray(0, n), ys: ys.subarray(0, n), line, xDomain, yDomain };
}

export const TH_CSV_HEADER = ["m", "med", "n"] as const;

export function thCsv(th: Pick<ThBlock, "groups">): string {
  const m = th.groups?.m ?? [];
  return csvText(
    TH_CSV_HEADER,
    m.map((v, i) => [v, th.groups.med[i], th.groups.n?.[i]]),
  );
}

/** «σ(c) = K·(a + b·c)»: the precision of the line in words (a, b, K), missing — «—». */
export function thFormula(th: Pick<ThBlock, "a" | "b" | "k">): string {
  return `a = ${formatNumber(th.a, 4)}, b = ${formatNumber(th.b, 4)}, K = ${formatNumber(th.k, 6)}, K·b = ${finite(th.k) && finite(th.b) ? formatNumber(th.k * th.b, 4) : "—"}`;
}

export const EXCLUDED_TITLES: Record<keyof Excluded, string> = {
  no_parent: "без исходной пробы",
  missing: "нет значения",
  negative: "отрицательные",
  zero: "сумма равна 0",
  below: "среднее ниже k_д·DL",
};

/** The pairs left out, by reason, in the order of ADR 0020 §4; every reason, its count as answered (none — «—»). */
export function excludedRows(excluded: Partial<Excluded> | null | undefined): [string, number | null][] {
  return (Object.keys(EXCLUDED_TITLES) as (keyof Excluded)[]).map((k) => [EXCLUDED_TITLES[k], finite(excluded?.[k]) ? (excluded?.[k] as number) : null]);
}

/** The sum of the pairs left out; null — no counts. */
export function excludedTotal(excluded: Partial<Excluded> | null | undefined): number | null {
  const v = Object.values(excluded ?? {}).filter(finite);
  return v.length ? v.reduce((a, b) => a + b, 0) : null;
}

/** «выполнен», «не выполнен», «—» (not assessed). */
export const verdictTitle = (passed: boolean | null | undefined): string => (passed === true ? "выполнен" : passed === false ? "не выполнен" : "—");
