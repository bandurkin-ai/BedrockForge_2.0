/**
 * The edit of the model with the mouse on the charts of the window «Вариография» (ТЗ 8.6.4; ADR 0022 §15 «Правка
 * мышью», decision 19): the nugget — a node at h = 0 (C0); the sill of each structure — a horizontal line at
 * C0 + Σ c up to it (its drag changes the contribution c of that structure only); the range — a vertical line at
 * a1, a2 or a3 of the structure on the chart of that axis of the model. The curves follow on the side of the
 * interface (`model.ts`, ≤ 50 ms a frame); the preview and the job wait for the release (`variographyStore.dragging`).
 * A range is drawn only where the axis of the chart is the axis of the structure (its angles are those of the
 * first, «общие оси»): elsewhere the range along the chart is not a1–a3. The downhole chart has the nugget and the
 * sills (no range: its direction follows the holes). Pure: no React; tested in node.
 */
import type { Scale } from "../../charts/core/axisScale";
import type { PlotBox } from "../../charts/core/PlotFrame";
import { effectiveStructures, type VModel } from "./model";

/** The chart a handle is drawn on: the index of the direction, or the downhole one. */
export type ChartKey = number | "downhole";

export type DragTarget = { kind: "nugget" } | { kind: "sill"; s: number } | { kind: "range"; s: number; axis: 1 | 2 | 3 };

export interface ChartHandle {
  /** «nugget», «sill:0», «range:1:a2» */
  id: string;
  target: DragTarget;
  /** a point (the nugget) or a line, px of the viewBox */
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  /** the hint of the handle */
  title: string;
}

/** The scales of a chart (`VariogramScales` of `charts/variogram/VariogramChart`). */
export interface DragScales {
  box: PlotBox;
  sx: Scale;
  sy: Scale;
  hMax: number;
}

/** The hit tolerance of a handle, px (the same as the ellipsoid handles, 16.1). */
export const HIT_PX = 8;
/** The largest range (ТЗ 8.6.4), m. */
export const RANGE_MAX = 100_000;

const RANGE_KEY = { 1: "a1", 2: "a2", 3: "a3" } as const;

/** Whether the axes of the structure `s` are the axes of the model (of its first structure). */
export function sameAxes(m: VModel, s: number): boolean {
  if (s === 0 || m.common_axes) return true;
  const a = m.structures[0];
  const b = m.structures[s];
  return !!a && !!b && a.ang1 === b.ang1 && a.ang2 === b.ang2 && a.ang3 === b.ang3;
}

const f = (x: number) => x.toLocaleString("ru-RU", { maximumSignificantDigits: 4 });

/**
 * The handles of a chart: `axis` — the axis 1–3 of the model the chart shows (null — a direction of its own or
 * downhole: no range). A handle whose position the chart cannot show (off the value axis) is not drawn.
 */
export function chartHandles(m: VModel, axis: 1 | 2 | 3 | null, sc: DragScales): ChartHandle[] {
  const out: ChartHandle[] = [];
  const { box, sx, sy } = sc;
  const x0 = sx.map(0);
  const yc0 = sy.map(m.c0);
  const inside = (y: number) => y >= box.y0 - 0.5 && y <= box.y1 + 0.5;
  if (x0 !== null && yc0 !== null && inside(yc0)) out.push({ id: "nugget", target: { kind: "nugget" }, x1: x0, y1: yc0, x2: x0, y2: yc0, title: `Самородок C0 = ${f(m.c0)}: тяните вверх или вниз` });
  let level = m.c0;
  const list = effectiveStructures(m);
  list.forEach((s, i) => {
    level += s.c;
    const y = sy.map(level);
    if (y !== null && inside(y)) out.push({ id: `sill:${i}`, target: { kind: "sill", s: i }, x1: box.x0, y1: y, x2: box.x1, y2: y, title: `Порог структуры ${i + 1}: C0 + Σc = ${f(level)} (вклад c = ${f(s.c)})` });
  });
  if (axis !== null) {
    list.forEach((s, i) => {
      if (!sameAxes(m, i)) return;
      const key = RANGE_KEY[axis];
      const x = sx.map(s[key]);
      if (x === null || x < box.x0 - 0.5 || x > box.x1 + 0.5) return;
      out.push({ id: `range:${i}:${key}`, target: { kind: "range", s: i, axis }, x1: x, y1: box.y0, x2: x, y2: box.y1, title: `Дальность ${key} структуры ${i + 1} = ${f(s[key])} м` });
    });
  }
  return out;
}

/** The distance from a point to a handle (a segment), px. */
export function handleDistance(h: ChartHandle, x: number, y: number): number {
  const dx = h.x2 - h.x1;
  const dy = h.y2 - h.y1;
  const len2 = dx * dx + dy * dy;
  const t = len2 > 0 ? Math.min(1, Math.max(0, ((x - h.x1) * dx + (y - h.y1) * dy) / len2)) : 0;
  return Math.hypot(x - (h.x1 + t * dx), y - (h.y1 + t * dy));
}

const PRIORITY: Record<DragTarget["kind"], number> = { nugget: 0, range: 1, sill: 2 };

/** The handle under a point: the nearest within `tol`; the nugget node wins over the lines, a range over a sill. */
export function hitHandle(handles: readonly ChartHandle[], x: number, y: number, tol = HIT_PX): ChartHandle | null {
  let best: ChartHandle | null = null;
  let key = Infinity;
  for (const h of handles) {
    const d = handleDistance(h, x, y);
    if (d > tol) continue;
    const k = PRIORITY[h.target.kind] * (tol + 1) + d;
    if (k < key) [best, key] = [h, k];
  }
  return best;
}

/** A dragged number to 3 significant digits (as the ranges of the ellipsoid handles, 16.1); a tiny one — as it is. */
export function round3(x: number): number {
  if (!Number.isFinite(x) || x === 0) return x;
  const p = 10 ** (2 - Math.floor(Math.log10(Math.abs(x))));
  return Math.round(x * p) / p + 0;
}

export interface DragLimits {
  /** the least range, m (L / 2 of the calculation; 0 — any positive) */
  minRange?: number;
  /** 3 significant digits (by default); false — the value as it is */
  round?: boolean;
}

/**
 * The model with the handle moved to the value (h, value) of the chart: the nugget C0 ≥ 0; the contribution of the
 * structure `s` so that its sill is at the value (c ≥ 0, the other structures unchanged); the range of the axis in
 * (0; 100 000] m and not below `minRange`. Ranges of the other axes, the angles and «общие оси» are kept.
 */
export function dragTo(m: VModel, t: DragTarget, h: number, value: number, lim: DragLimits = {}): VModel {
  const r = (x: number) => (lim.round === false ? x : round3(x));
  if (t.kind === "nugget") return { ...m, c0: Number.isFinite(value) ? Math.max(0, r(value)) : m.c0 };
  if (t.kind === "sill") {
    if (!m.structures[t.s] || !Number.isFinite(value)) return m;
    let below = m.c0;
    for (let k = 0; k < t.s; k++) below += m.structures[k].c;
    const c = Math.max(0, r(value - below));
    return { ...m, structures: m.structures.map((s, k) => (k === t.s ? { ...s, c } : s)) };
  }
  if (!m.structures[t.s] || !Number.isFinite(h)) return m;
  const least = Math.max(lim.minRange ?? 0, 1e-6);
  const a = Math.min(RANGE_MAX, Math.max(least, r(h)));
  const key = RANGE_KEY[t.axis];
  return { ...m, structures: m.structures.map((s, k) => (k === t.s ? { ...s, [key]: a } : s)) };
}

/** The value under a point of the chart: h of the x, the value of the y. */
export function valueAt(sc: DragScales, x: number, y: number): { h: number; value: number } {
  return { h: sc.sx.invert(x), value: sc.sy.invert(y) };
}
