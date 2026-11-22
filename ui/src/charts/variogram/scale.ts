/**
 * The pure part of the variogram charts (ТЗ 8.6.4; ADR 0022 §5, §15, decisions 4, 8, 19): which value of the answer
 * a type plots (γ; 1 − ρ for the correlogram; the pairwise relative over `n_pw`), the experimental points (a lag
 * without pairs — `null` — is dropped, never drawn as 0), the size of a mark by its number of pairs, the domains,
 * the curve of the model (the formulas of `modules/variography/model.ts`, not repeated here), the rose of ranges and
 * the variogram map (readers of `POST /variography/map`, geometry, colour scale, raster; an empty cell is not
 * drawn — it never takes the colour of zero), CSV. Tested in node.
 *
 * Orientation of the rose and the map (the plane of a1, a2; theta from a1 to a2): a plan seen from above — a1 points
 * up, a2 to the LEFT, theta counter-clockwise from a1: at zero angles a2 is west (−1; 0; 0) and u3 = a1 × a2 points
 * up (the decision of the orchestrator on the mirror of the plane, 16.4–16.5). The map's u (along a1) is the
 * vertical axis, v (along a2) the horizontal one growing to the left. The labels are the world az / plunge of the
 * rays (of the fan of the same answer).
 */
import { formatNumber, type Domain } from "../core/axisScale";
import { csvText, type CsvCell } from "../core/csv";
import { rampColor, rgba32 } from "../core/palette";
import { niceTicks } from "../column/scale";
import type { DirectionCurve } from "../../modules/variography/api";
import { axisOf, direction, distances, gammaAlong, longestRange, sill, type ModelType, type VModel } from "../../modules/variography/model";
import type { Vec3 } from "../../viz/core/vec3";

/** The lags of one curve: a direction, an axis, the downhole one or a ray of the fan. */
export type CurveData = Pick<DirectionCurve, "lags" | "h" | "n" | "gamma" | "rho" | "pairwise" | "n_pw" | "few">;

const finite = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);

/** The values a type plots: γ (also of Y for `nscore`), 1 − ρ, the pairwise relative; `null` stays `null`. */
export function measureValues(c: CurveData, type: ModelType): (number | null)[] {
  if (type === "correlogram") return c.rho.map((r) => (finite(r) ? 1 - r : null));
  if (type === "pairwise") return c.pairwise.map((v) => (finite(v) ? v : null));
  return c.gamma.map((v) => (finite(v) ? v : null));
}

/** The number of pairs behind each value: `n_pw` for the pairwise relative, else `n`. */
export const measurePairs = (c: CurveData, type: ModelType): number[] => (type === "pairwise" ? c.n_pw : c.n);

/** The title of the value axis of a type. */
export function measureTitle(type: ModelType): string {
  if (type === "correlogram") return "1 − ρ(h)";
  if (type === "pairwise") return "γ парная относительная";
  if (type === "nscore") return "γ(h) нормальных оценок";
  return "γ(h)";
}

export interface ExpPoint {
  /** the lag number (0-based) */
  k: number;
  /** the mean distance of the pairs */
  h: number;
  value: number;
  n: number;
  /** N < min_pairs: a hollow mark, not in the fit */
  few: boolean;
}

/** The points of a curve: lags with a distance and a value; a lag without pairs (`null`, NaN) is not a point. */
export function expPoints(c: CurveData, type: ModelType): ExpPoint[] {
  const vals = measureValues(c, type);
  const pairs = measurePairs(c, type);
  const out: ExpPoint[] = [];
  for (let k = 0; k < vals.length; k++) {
    const h = c.h[k];
    const v = vals[k];
    if (!finite(h) || !finite(v)) continue;
    out.push({ k, h, value: v, n: pairs[k] ?? 0, few: c.few[k] === true });
  }
  return out;
}

/** The radius of a mark, 2,5…7 px by √(N / N_max) (ТЗ 8.6.4 «с числом пар»). */
export function pointRadius(n: number, nMax: number): number {
  if (!(n > 0) || !(nMax > 0)) return 2.5;
  return 2.5 + 4.5 * Math.sqrt(Math.min(1, n / nMax));
}

/** The number of pairs as a label: «412», «12,3 тыс.», «1,2 млн». */
export function pairsText(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (n >= 1e6) return `${formatNumber(n / 1e6, 3)} млн`;
  if (n >= 1e4) return `${formatNumber(n / 1e3, 3)} тыс.`;
  return String(Math.round(n));
}

/**
 * The level of the variance line: σ² for γ (Y of `nscore` too), 1 for the correlogram (1 − ρ → 1); the pairwise
 * relative has no such level (null). σ² unknown — null.
 */
export function varianceLevel(type: ModelType, sigma2: number | null | undefined): { value: number; label: string } | null {
  if (type === "correlogram") return { value: 1, label: "1" };
  if (type === "pairwise") return null;
  return finite(sigma2) ? { value: sigma2, label: "σ²" } : null;
}

/** The direction of the model curve of a lag curve: an axis follows the model (its angles may be dragged), else az, plunge. */
export function curveDirection(c: Pick<DirectionCurve, "axis" | "az" | "plunge">, model: Pick<VModel, "structures"> | null | undefined): Vec3 {
  if (c.axis !== null && model && model.structures.length) return axisOf(model, c.axis);
  return direction(c.az, c.plunge);
}

export interface Line {
  h: number[];
  value: number[];
}

/** The model along u: 0 … h_max, n + 1 distances; γ(0) = 0 (formulas of `model.ts`). */
export function modelLine(model: VModel, u: Vec3, hMax: number, n = 100): Line {
  const h = distances(hMax, n);
  return { h, value: gammaAlong(model, h, u) };
}

/** h_max of a chart: the farthest lag (centre) or mean distance; no lags — 1,5 × the longest range of the model. */
export function chartHMax(curves: readonly CurveData[], model?: VModel | null): number | null {
  let hi = 0;
  for (const c of curves) {
    for (const x of c.lags) if (finite(x) && x > hi) hi = x;
    for (const x of c.h) if (finite(x) && x > hi) hi = x;
  }
  if (hi > 0) return hi * 1.05;
  const r = model ? longestRange(model) : 0;
  return r > 0 ? r * 1.5 : null;
}

/** The value domain from 0: the points, the model, the variance and the sill lines; nothing — null. */
export function valueDomain(points: Iterable<number>, extra: Iterable<number | null | undefined> = []): Domain | null {
  let hi = -Infinity;
  for (const v of points) if (finite(v) && v > hi) hi = v;
  for (const v of extra) if (finite(v) && v > hi) hi = v;
  if (hi === -Infinity) return null;
  return { min: 0, max: hi > 0 ? hi * 1.08 : 1 };
}

/** Pixels of a polyline split where a value is missing (a gap stays a gap). */
export function segments(xs: readonly number[], ys: readonly (number | null)[], mx: (v: number) => number | null, my: (v: number | null) => number | null): string[] {
  const out: string[] = [];
  let cur: string[] = [];
  for (let i = 0; i < xs.length; i++) {
    const px = mx(xs[i]);
    const py = Number.isFinite(ys[i] ?? NaN) ? my(ys[i]) : null;
    if (px === null || py === null) {
      if (cur.length > 1) out.push(cur.join(" "));
      cur = [];
      continue;
    }
    cur.push(`${px.toFixed(1)},${py.toFixed(1)}`);
  }
  if (cur.length > 1) out.push(cur.join(" "));
  return out;
}

/** The model's sill C0 + Σc (a line), null — no model. */
export const modelSill = (m: VModel | null | undefined): number | null => (m && (m.structures.length || m.c0 > 0) ? sill(m) : null);

// ---- the rose of ranges ----

export interface RoseRay {
  /** the angle in the plane from a1 to a2, ° */
  theta: number;
  /** the world direction of the ray (of the fan of the same θ); absent — unknown */
  az?: number;
  plunge?: number;
  /** the first distance where γ̂ ≥ q·σ²; null — not reached */
  range: number | null;
  /** not reached within h_max («> h_max») */
  beyond: boolean;
}

export interface RoseData {
  q: number | null;
  level: number | null;
  rays: RoseRay[];
}

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** `rose` of the answer of `/map`; null — not ordered. */
export function readRose(raw: unknown): RoseData | null {
  if (raw === null || raw === undefined) return null;
  const o = obj(raw);
  const rays = (Array.isArray(o.rays) ? o.rays : []).map((r): RoseRay => {
    const x = obj(r);
    const range = numOrNull(x.range);
    return { theta: numOrNull(x.theta) ?? 0, range: range !== null && range >= 0 ? range : null, beyond: x.beyond === true || range === null };
  });
  return { q: numOrNull(o.q), level: numOrNull(o.level), rays };
}

export const mod180 = (t: number): number => ((t % 180) + 180) % 180;

/** A point of the plane at the angle theta (a1 up, a2 left, counter-clockwise) and distance r → screen offset (y down). */
export function polar(theta: number, r: number): [number, number] {
  const t = (theta * Math.PI) / 180;
  return [-r * Math.sin(t) + 0, -r * Math.cos(t) + 0];
}

/** The angle theta of a screen offset (a1 up, a2 left), 0…180 (a direction has no sign). */
export const thetaOf = (dx: number, dy: number): number => mod180((Math.atan2(-dx, -dy) * 180) / Math.PI);

export interface RoseVertex {
  theta: number;
  range: number;
}

/**
 * The outline of the rose: each ray also at theta + 180°, in angle order; split where a ray has no range (a gap is
 * not closed over — the range there is unknown). One run over all rays is closed.
 */
export function roseRuns(rays: readonly RoseRay[]): { runs: RoseVertex[][]; closed: boolean } {
  const both = [...rays.map((r) => ({ ...r, theta: mod180(r.theta) })), ...rays.map((r) => ({ ...r, theta: mod180(r.theta) + 180 }))].sort((a, b) => a.theta - b.theta);
  const runs: RoseVertex[][] = [];
  let cur: RoseVertex[] = [];
  for (const r of both) {
    if (r.range === null) {
      if (cur.length) runs.push(cur);
      cur = [];
    } else cur.push({ theta: r.theta, range: r.range });
  }
  if (cur.length) runs.push(cur);
  const closed = both.length > 0 && both.every((r) => r.range !== null);
  // the run through 360° → 0° is one when the first and the last ray both have a range
  if (!closed && runs.length > 1 && both[0].range !== null && both[both.length - 1].range !== null) {
    const last = runs.pop() as RoseVertex[];
    runs[0] = [...last, ...runs[0]];
  }
  return { runs, closed };
}

/** The radius of the rose: h_max given, else the longest range × 1,15; nothing — null. */
export function roseRadius(rays: readonly RoseRay[], hMax?: number | null): number | null {
  if (finite(hMax) && hMax > 0) return hMax;
  const r = rays.reduce((a, x) => (x.range !== null && x.range > a ? x.range : a), 0);
  return r > 0 ? r * 1.15 : null;
}

/** The ray nearest to theta (mod 180°); none — null. */
export function nearestRay(rays: readonly RoseRay[], theta: number): RoseRay | null {
  let best: RoseRay | null = null;
  let bd = Infinity;
  for (const r of rays) {
    const d = Math.abs(mod180(r.theta - theta + 90) - 90);
    if (d < bd) [best, bd] = [r, d];
  }
  return best;
}

// ---- the variogram map ----

export interface VarMapData {
  nu: number;
  nv: number;
  cell: number;
  thickness: number | null;
  min_pairs: number;
  /** the centres along a1, along a2 */
  u: number[];
  v: number[];
  /** gamma[j][i]: row j by v (bottom up), column i by u; N < min_pairs — null */
  gamma: (number | null)[][];
  n: number[][];
}

const numsOf = (v: unknown): (number | null)[] => (Array.isArray(v) ? v.map(numOrNull) : []);

/** `map` of the answer of `/map`; null — not ordered. `thickness` "inf" — null (a projection). */
export function readVarMap(raw: unknown): VarMapData | null {
  if (raw === null || raw === undefined) return null;
  const o = obj(raw);
  const u = numsOf(o.u).map((x) => x ?? NaN);
  const v = numsOf(o.v).map((x) => x ?? NaN);
  const rows = (k: unknown) => (Array.isArray(k) ? k.map(numsOf) : []);
  const gamma = rows(o.gamma).map((r) => r.map((x) => (finite(x) ? x : null)));
  const n = rows(o.n).map((r) => r.map((x) => (finite(x) ? x : 0)));
  const cell = numOrNull(o.cell) ?? (u.length > 1 ? Math.abs(u[1] - u[0]) : 1);
  return { nu: numOrNull(o.nu) ?? (u.length - 1) / 2, nv: numOrNull(o.nv) ?? (v.length - 1) / 2, cell, thickness: numOrNull(o.thickness), min_pairs: numOrNull(o.min_pairs) ?? 1, u, v, gamma, n };
}

/** The γ of a cell; out of the grid, empty — null. */
export const mapCell = (m: VarMapData, i: number, j: number): number | null => m.gamma[j]?.[i] ?? null;

/** The extent of the map: u (vertical, up) and v (horizontal, to the left), the outer edges of the cells. */
export function mapExtent(m: VarMapData): { u: Domain; v: Domain } | null {
  const fu = m.u.filter(Number.isFinite);
  const fv = m.v.filter(Number.isFinite);
  if (!fu.length || !fv.length) return null;
  const h = m.cell / 2;
  return { u: { min: Math.min(...fu) - h, max: Math.max(...fu) + h }, v: { min: Math.min(...fv) - h, max: Math.max(...fv) + h } };
}

/** The colour scale of the map: 0 … max(γ, σ²); no value — null. Zero is Cobalt, an empty cell has no colour. */
export function mapDomain(m: VarMapData, sigma2?: number | null): Domain | null {
  let hi = -Infinity;
  for (const g of m.gamma.flat()) if (finite(g) && g > hi) hi = g;
  if (hi === -Infinity) return null;
  if (finite(sigma2) && sigma2 > hi) hi = sigma2;
  return { min: 0, max: hi > 0 ? hi : 1 };
}

/** The colour of a value on the scale; `null` — none (an empty cell is not painted). */
export function mapColor(v: number | null | undefined, d: Domain | null): string | null {
  if (!finite(v) || !d) return null;
  const span = d.max - d.min;
  return rampColor(span > 0 ? (v - d.min) / span : 0);
}

/** Ticks of the colour bar. */
export const mapTicks = (d: Domain, count = 5): number[] => niceTicks(d.min, d.max, count).filter((t) => t >= d.min - 1e-12 && t <= d.max + 1e-12);

/**
 * The cells into a raster w × h (Uint32 RGBA, row 0 at the top): u along a1 goes up, v along a2 to the left.
 * An empty cell stays 0 (transparent: the hatching under it shows); the number of painted cells is returned.
 */
export function rasterMap(buf: Uint32Array, w: number, h: number, m: VarMapData, d: Domain | null): number {
  const ext = mapExtent(m);
  if (!ext || !d) return 0;
  const kx = w / (ext.v.max - ext.v.min);
  const ky = h / (ext.u.max - ext.u.min);
  const half = m.cell / 2;
  let painted = 0;
  for (let j = 0; j < m.gamma.length; j++) {
    const vc = m.v[j];
    for (let i = 0; i < m.gamma[j].length; i++) {
      const col = mapColor(m.gamma[j][i], d);
      const uc = m.u[i];
      if (!col || !Number.isFinite(uc) || !Number.isFinite(vc)) continue;
      const xa = Math.max(0, Math.round((ext.v.max - (vc + half)) * kx));
      const xb = Math.min(w, Math.round((ext.v.max - (vc - half)) * kx));
      const ya = Math.max(0, Math.round((ext.u.max - (uc + half)) * ky));
      const yb = Math.min(h, Math.round((ext.u.max - (uc - half)) * ky));
      const px = rgba32(col);
      for (let y = ya; y < yb; y++) buf.fill(px, y * w + xa, y * w + xb);
      painted++;
    }
  }
  return painted;
}

/** The cell (i by u, j by v) under a point of the plane; outside — null. */
export function cellAt(m: VarMapData, uu: number, vv: number): { i: number; j: number } | null {
  const pick = (cs: number[], x: number) => {
    let best = -1;
    let bd = Infinity;
    cs.forEach((c, k) => {
      const dd = Math.abs(c - x);
      if (dd < bd) [best, bd] = [k, dd];
    });
    return bd <= m.cell / 2 + 1e-9 ? best : -1;
  };
  const i = pick(m.u, uu);
  const j = pick(m.v, vv);
  return i < 0 || j < 0 ? null : { i, j };
}

// ---- CSV ----

/** CSV of the curves: one row per lag; an empty value stays empty (never 0). */
export function curvesCsv(series: readonly { label: string; curve: CurveData }[], type: ModelType): string {
  const rows: CsvCell[][] = [];
  for (const s of series) {
    const vals = measureValues(s.curve, type);
    const pairs = measurePairs(s.curve, type);
    s.curve.lags.forEach((lag, k) => rows.push([s.label, k + 1, lag, s.curve.h[k], vals[k], pairs[k] ?? null, s.curve.few[k] ? "да" : ""]));
  }
  return csvText(["Направление", "Лаг", "Центр лага", "h среднее", measureTitle(type), "Число пар", "Мало пар"], rows);
}

/** CSV of the map: one row per cell. */
export function mapCsv(m: VarMapData): string {
  const rows: CsvCell[][] = [];
  m.gamma.forEach((row, j) => row.forEach((g, i) => rows.push([m.u[i], m.v[j], g, m.n[j]?.[i] ?? null])));
  return csvText(["u (a1)", "v (a2)", "γ", "Число пар"], rows);
}

/** CSV of the rose. */
export function roseCsv(r: RoseData): string {
  return csvText(["θ, °", "Азимут, °", "Погружение, °", "Дальность", "Не достигнута"], r.rays.map((x) => [x.theta, x.az ?? null, x.plunge ?? null, x.range, x.beyond ? "да" : ""]));
}
