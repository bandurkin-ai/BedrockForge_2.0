/**
 * What the trend commands, the tree, the windows «Тренд» and the 3D know (ТЗ 7.35 Д-15; ADR 0022 §10): the trends of
 * the project (read when a project opens, kept by `geostat.changed {kind: "trend"}`), the row chosen in the tree, and
 * the edit of every open window «Тренд» (its values as typed — text, «12,5» is a number; the origin; shown in 3D or
 * not). The ellipsoid of a trend is the one of `viz/ellipsoid` with a single structure: a1 = `size`, a2 = size·r2,
 * a3 = size·r3 — its axis ends change the ratios (a1 — the size shown too), its arcs the angles (`modelOf` /
 * `withModel`). The trends shown in 3D — the view key `ellipsoids` `[{object, visible}]` (as the core reads it:
 * `viewstate/keys.py`; ≤ 50; a model of a variogram is an `object` of the same key). The variogram window gives
 * «Сохранить как тренд» its arguments (`trendArgsOfWork`). No React.
 */
import { Store } from "../../shell/store";
import type { Model } from "../../viz/ellipsoid/drag";
import type { Vec3 } from "../../viz/core/vec3";
import { effectiveStructures } from "../variography/model";
import { chosenStructure, ellipsoidCentre, type Work } from "../variography/state";
import type { Trend, TrendBody, TrendKind, TrendOrigin } from "./api";

export const TREND_WINDOW = "trend";
export const DEFAULT_COLOR = "#f46a1a"; // Copper Orange (docs/brand, ADR 0006): the trends apart from the models (Cobalt)
export const DEFAULT_SIZE = 100;

/** The values of the form as typed (a number with «,» or «.»; empty — the default of the core, the centre — none). */
export interface TrendValues {
  name: string;
  ang1: string;
  ang2: string;
  ang3: string;
  r2: string;
  r3: string;
  size: string;
  cx: string;
  cy: string;
  cz: string;
  color: string;
}

export const RADIUS_RANGE = { lo: 10, hi: 5000, dflt: 200 } as const;

export type SourceType = "measurements" | "mesh";

/** The source of a structural trend as typed in the form (`structural.ts`). */
export interface StructuralValues {
  type: SourceType;
  setId: number | null;
  /** the key of the table (the id as text) */
  table: string;
  alpha: string;
  dipdir: string;
  /** empty — the middle of FROM–TO of the row */
  depth: string;
  wireframeId: number | null;
  rN: string;
  radius: string;
}

export const EMPTY_STRUCTURAL: StructuralValues = { type: "measurements", setId: null, table: "", alpha: "", dipdir: "", depth: "", wireframeId: null, rN: "0", radius: String(RADIUS_RANGE.dflt) };

/** The edit of a window «Тренд». */
export interface TrendEdit {
  /** the trend edited; null — a new one */
  trendId: number | null;
  /** global (angles, ratios) or structural (the source, R_тр, ratios); of a stored trend — its own, never changed */
  kind: TrendKind;
  /** the source and R_тр of a structural trend */
  structural: StructuralValues;
  /** the version of that trend read or saved (`expected_version` of `trend.save`); absent — unknown */
  version?: number | null;
  values: TrendValues;
  /** of the stored trend (a new one — manual, or «из эллипсоида» of a variogram window) */
  origin: TrendOrigin;
  /** a new trend filled from a model and not edited since: saved as a copy of the model (`model_id`) */
  fromModel: { model_id: number; structure: number } | null;
  /** its ellipsoid in 3D (with the handles) */
  show: boolean;
  dirty: boolean;
}

export interface TrendsState {
  trends: Trend[];
  loaded: boolean;
  selected: number | null;
  /** window id → its edit */
  edits: Record<string, TrendEdit>;
  /** a handle of a trend's ellipsoid dragged (its window id); null — none */
  dragging: string | null;
  /** structural trend id → the discs in 3D now («Показано N из M»); written by `discScene.ts` */
  discs: Record<number, DiscInfo>;
}

/** The discs of a structural trend drawn: shown / of all (thinned: ≤ 50 000 measurements, ≤ 10 000 faces), left out as not a number. */
export interface DiscInfo {
  n: number;
  total: number;
  thinned: boolean;
  skipped: number;
}

export const trendsStore = new Store<TrendsState>({ trends: [], loaded: false, selected: null, edits: {}, dragging: null, discs: {} });

export const trendsByName = (rows: readonly Trend[]): Trend[] => [...rows].sort((a, b) => a.name.localeCompare(b.name, "ru") || a.id - b.id);
export const findTrend = (id: number): Trend | undefined => trendsStore.get().trends.find((t) => t.id === id);
export const editOf = (win: string): TrendEdit | null => trendsStore.get().edits[win] ?? null;

export function setTrends(trends: Trend[]): void {
  trendsStore.set({ trends, loaded: true });
}

export function upsertTrend(t: Trend): void {
  trendsStore.set((s) => ({ trends: [...s.trends.filter((x) => x.id !== t.id), t] }));
}

export function dropTrend(id: number): void {
  trendsStore.set((s) => ({ trends: s.trends.filter((x) => x.id !== id), selected: s.selected === id ? null : s.selected }));
}

export function resetTrends(): void {
  trendsStore.set({ trends: [], loaded: false, selected: null, edits: {}, dragging: null, discs: {} });
}

export function setDiscInfo(id: number, info: DiscInfo | null): void {
  if (info === null && !trendsStore.get().discs?.[id]) return; // nothing to forget: no event of the store
  trendsStore.set((s) => {
    const { [id]: _old, ...rest } = s.discs ?? {}; // a store set without `discs` (an older reset) reads as none
    return { discs: info ? { ...rest, [id]: info } : rest };
  });
}

/** «Показано 4 120 дисков из 52 300 (прорежено)». */
export const discInfoText = (i: DiscInfo): string => `Показано дисков: ${i.n.toLocaleString("ru-RU")} из ${i.total.toLocaleString("ru-RU")}${i.thinned ? " (прорежено для 3D)" : ""}${i.skipped ? `; без положения или нормали: ${i.skipped.toLocaleString("ru-RU")}` : ""}`;

export const selectTrend = (id: number | null): void => trendsStore.set({ selected: id });

export function setEdit(win: string, patch: Partial<TrendEdit> | ((e: TrendEdit) => Partial<TrendEdit>)): TrendEdit {
  const cur = editOf(win) ?? newEdit();
  const next = { ...cur, ...(typeof patch === "function" ? patch(cur) : patch) };
  trendsStore.set((s) => ({ edits: { ...s.edits, [win]: next } }));
  return next;
}

export function forgetEdit(win: string): void {
  trendsStore.set((s) => {
    const { [win]: _gone, ...edits } = s.edits;
    return { edits };
  });
}

export const setDragging = (win: string | null): void => trendsStore.set({ dragging: win });

/** The window «Тренд» of a stored trend, if open. */
export const windowOfTrend = (id: number): string | null => Object.entries(trendsStore.get().edits).find(([, e]) => e.trendId === id)?.[0] ?? null;

// ---- numbers and the values of the form ----

/** A number as the form shows it: 12 significant digits at most, «,» as the decimal sign. */
export function numText(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "";
  const r = Number(v.toPrecision(12));
  return String(Object.is(r, -0) ? 0 : r).replace(".", ",");
}

/** A number typed: «12,5», «12.5», « −3 »; empty or junk — null. */
export function parseNum(text: string): number | null {
  const t = text.trim().replace(/\s/g, "").replace(",", ".").replace("−", "-");
  if (!t || !/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(t)) return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : null;
}

export const EMPTY_VALUES: TrendValues = { name: "", ang1: "0", ang2: "0", ang3: "0", r2: "1", r3: "1", size: String(DEFAULT_SIZE), cx: "", cy: "", cz: "", color: DEFAULT_COLOR };

export const newEdit = (patch: Partial<TrendEdit> = {}): TrendEdit => ({ trendId: null, kind: "global", structural: { ...EMPTY_STRUCTURAL }, values: { ...EMPTY_VALUES }, origin: { type: "manual" }, fromModel: null, show: true, dirty: false, ...patch });

export function valuesOf(t: Trend): TrendValues {
  return {
    name: t.name,
    ang1: numText(t.ang1),
    ang2: numText(t.ang2),
    ang3: numText(t.ang3),
    r2: numText(t.ratios[1]),
    r3: numText(t.ratios[2]),
    size: numText(t.size),
    cx: t.centre ? numText(t.centre[0]) : "",
    cy: t.centre ? numText(t.centre[1]) : "",
    cz: t.centre ? numText(t.centre[2]) : "",
    color: t.color,
  };
}

/** The shape of a trend: what a body, the 3D and the handles take. */
export interface TrendShape {
  name: string;
  ang1: number;
  ang2: number;
  ang3: number;
  ratios: [number, number, number];
  size: number;
  centre: Vec3 | null;
  color: string;
}

export type TrendParam = "name" | "ang1" | "ang2" | "ang3" | "r2" | "r3" | "size" | "centre" | "color";
export type Checked = { ok: true; shape: TrendShape } | { ok: false; param: TrendParam; error: string };

/** The ranges of the core (`trend.py`): the angles of 8.6.3, the ratios (0; 10⁶], the size (0; 10⁷] m. */
export const RANGES = {
  ang1: { lo: 0, hi: 360, text: "Азимут ang1: число от 0 до 360°" },
  ang2: { lo: -90, hi: 90, text: "ang2 (−погружение): число от −90 до 90°" },
  ang3: { lo: -180, hi: 180, text: "Поворот ang3: число от −180 до 180°" },
  r2: { lo: 0, hi: 1e6, text: "Отношение a2 / a1: число больше 0 до 1 000 000" },
  r3: { lo: 0, hi: 1e6, text: "Отношение a3 / a1: число больше 0 до 1 000 000" },
  size: { lo: 0, hi: 1e7, text: "Размер показа: число метров больше 0 до 10 000 000" },
} as const;

const OPEN_LO: readonly TrendParam[] = ["r2", "r3", "size"];

/** The values checked before a request (out of range never reaches the core) → the shape. */
export function checkValues(v: TrendValues): Checked {
  const got: Partial<Record<keyof typeof RANGES, number>> = {};
  for (const k of Object.keys(RANGES) as (keyof typeof RANGES)[]) {
    const x = parseNum(v[k]);
    const r = RANGES[k];
    if (x === null || x > r.hi || x < r.lo || (OPEN_LO.includes(k) && x <= r.lo)) return { ok: false, param: k, error: r.text };
    got[k] = x;
  }
  const xyz = [v.cx, v.cy, v.cz].map(parseNum);
  const typed = [v.cx, v.cy, v.cz].filter((s) => s.trim() !== "").length;
  if (typed && (typed < 3 || xyz.some((x) => x === null))) return { ok: false, param: "centre", error: "Центр: три числа X, Y, Z или ни одного (тогда — центр вида)" };
  const color = v.color.trim();
  if (!/^#[0-9a-f]{6}$/i.test(color)) return { ok: false, param: "color", error: "Цвет: #rrggbb" };
  return {
    ok: true,
    shape: {
      name: v.name.trim(),
      ang1: got.ang1 === 360 ? 0 : got.ang1!,
      ang2: got.ang2!,
      ang3: got.ang3!,
      ratios: [1, got.r2!, got.r3!],
      size: got.size!,
      centre: typed ? (xyz as Vec3) : null,
      color: color.toLowerCase(),
    },
  };
}

/** The ellipsoid of a shape for the handles: one structure, a1 = size, a2 = size·r2, a3 = size·r3. */
export function modelOf(s: Pick<TrendShape, "ang1" | "ang2" | "ang3" | "ratios" | "size">): Model {
  return { angles: { ang1: s.ang1, ang2: s.ang2, ang3: s.ang3 }, ranges: { a1: s.size, a2: s.size * s.ratios[1], a3: s.size * s.ratios[2] } };
}

/** The values after a drag of a handle: the angles; size = a1, ratios = a2 / a1, a3 / a1. */
export function withModel(v: TrendValues, m: Model): TrendValues {
  const a1 = m.ranges.a1;
  return { ...v, ang1: numText(m.angles.ang1), ang2: numText(m.angles.ang2), ang3: numText(m.angles.ang3), size: numText(a1), r2: numText(m.ranges.a2 / a1), r3: numText(m.ranges.a3 / a1) };
}

/** The values of the structure `index` (from 0) of a variogram model (a copy of its axes, Д-15). */
export function valuesOfStructure(v: TrendValues, s: { ang1: number; ang2: number; ang3: number; a1: number; a2: number; a3: number }): TrendValues {
  return withModel(v, { angles: { ang1: s.ang1, ang2: s.ang2, ang3: s.ang3 }, ranges: { a1: s.a1, a2: s.a2, a3: s.a3 } });
}

/** The arguments of `trend.save` of an edit; a reason when the values are wrong. */
export function saveArgs(e: TrendEdit): { args: Record<string, unknown> } | { param: TrendParam; error: string } {
  const c = checkValues(e.values);
  if (!c.ok) return { param: c.param, error: c.error };
  const s = c.shape;
  const args: Record<string, unknown> = { kind: "global", size: s.size, color: s.color };
  if (s.name) args.name = s.name;
  if (e.trendId !== null) args.trend_id = e.trendId;
  if (e.trendId !== null && typeof e.version === "number") args.expected_version = e.version;
  if (s.centre) args.centre = s.centre.map(numText).join("; ");
  else if (e.trendId !== null) args.centre = ""; // emptied: the centre of the view
  if (e.fromModel && e.trendId === null) return { args: { ...args, model_id: e.fromModel.model_id, structure: e.fromModel.structure } };
  Object.assign(args, { ang1: s.ang1, ang2: s.ang2, ang3: s.ang3, ratios: `${numText(s.ratios[1])}; ${numText(s.ratios[2])}` });
  if (e.trendId === null && e.origin.type === "ellipsoid") args.origin = "ellipsoid";
  return { args };
}

// ---- the arguments of `trend.save` ----

const given = (a: Record<string, unknown>, k: string) => a[k] !== undefined && a[k] !== null && (typeof a[k] !== "string" || (a[k] as string).trim() !== "");

/** «0,36; 0,05», «1; 0,36; 0,05», [0.36, 0.05] → [1, r2, r3]; null — not given; a string — what is wrong. */
export function ratiosArg(raw: unknown): [number, number, number] | null | string {
  if (raw === undefined || raw === null || raw === "") return null;
  const list = Array.isArray(raw) ? raw.map((x) => (typeof x === "number" ? x : parseNum(String(x)))) : String(raw).split(/[;\s]+/).filter(Boolean).map(parseNum);
  const two = list.length === 3 ? (list[0] === 1 ? list.slice(1) : null) : list.length === 2 ? list : null;
  if (!two || two.some((x) => x === null || x <= 0 || x > 1e6)) return "Отношения: a2 / a1 и a3 / a1 через «;» — числа больше 0 до 1 000 000 (первое из трёх — ровно 1)";
  return [1, two[0]!, two[1]!];
}

/** «x; y; z» or [x, y, z] → the centre; "" — emptied (null); undefined — not given; a string — what is wrong. */
export function centreArg(raw: unknown): Vec3 | null | undefined | string {
  if (raw === undefined) return undefined;
  if (raw === null || raw === "") return null;
  const list = Array.isArray(raw) ? raw.map((x) => (typeof x === "number" ? x : parseNum(String(x)))) : String(raw).split(/[;\s]+/).filter(Boolean).map(parseNum);
  if (list.length !== 3 || list.some((x) => x === null || !Number.isFinite(x))) return "Центр: три числа X; Y; Z";
  return list as Vec3;
}

/** The body of `POST` / `PUT` from the arguments of `trend.save` (checked; out of range never reaches the core). */
export function bodyOfArgs(a: Record<string, unknown>): { body: TrendBody } | { param: string; error: string } {
  const body: TrendBody = {};
  if (given(a, "kind") && a.kind !== "global") {
    if (a.kind === "structural") return { param: "kind", error: "Структурный тренд задаётся источником (замеры или каркас): см. описание trend.save" };
    return { param: "kind", error: `Вид тренда «${String(a.kind)}» неизвестен: global` };
  }
  if (given(a, "name")) body.name = String(a.name).trim();
  for (const k of ["ang1", "ang2", "ang3", "size"] as const) {
    if (!given(a, k)) continue;
    const x = typeof a[k] === "number" ? (a[k] as number) : parseNum(String(a[k]));
    const r = RANGES[k];
    if (x === null || !Number.isFinite(x) || x > r.hi || x < r.lo || (k === "size" && x <= 0)) return { param: k, error: r.text };
    body[k] = k === "ang1" && x === 360 ? 0 : x;
  }
  const ratios = ratiosArg(a.ratios);
  if (typeof ratios === "string") return { param: "ratios", error: ratios };
  if (ratios) body.ratios = ratios;
  const centre = centreArg(a.centre);
  if (typeof centre === "string") return { param: "centre", error: centre };
  if (centre !== undefined) body.centre = centre;
  if (given(a, "color")) {
    const c = String(a.color).trim();
    if (!/^#[0-9a-f]{6}$/i.test(c)) return { param: "color", error: "Цвет: #rrggbb, например #f46a1a" };
    body.color = c.toLowerCase();
  }
  if (given(a, "model_id")) {
    if (typeof a.model_id !== "number" || !Number.isInteger(a.model_id)) return { param: "model_id", error: "model_id — номер модели вариограммы" };
    body.model_id = a.model_id;
  }
  if (given(a, "structure")) {
    const k = Number(a.structure);
    if (body.model_id === undefined) return { param: "structure", error: "structure задаётся вместе с model_id" };
    if (!Number.isInteger(k) || k < 1) return { param: "structure", error: "structure — номер структуры модели с 1" };
    body.structure = k;
  }
  if (a.origin === "ellipsoid" || a.origin === "manual") body.origin = { type: a.origin };
  return { body };
}

/** The arguments of «Сохранить как тренд» of a variogram window: a saved model unchanged — a copy of its structure
 * (`model_id`, the origin «model»); else the axes of the structure chosen of the working model, the origin
 * «ellipsoid»; the centre of its ellipsoid; null — no structure with positive ranges. */
export function trendArgsOfWork(w: Pick<Work, "model" | "modelId" | "dirty" | "ellipsoid" | "defaults">): Record<string, unknown> | null {
  const list = effectiveStructures(w.model);
  const index = chosenStructure(w);
  const s = list[index];
  if (!s || ![s.a1, s.a2, s.a3].every((x) => Number.isFinite(x) && x > 0)) return null;
  const centre = ellipsoidCentre(w as Work);
  const where = centre ? { centre: centre.map(numText).join("; ") } : {};
  if (w.modelId !== null && !w.dirty) return { kind: "global", model_id: w.modelId, structure: index + 1, ...where };
  return { kind: "global", ang1: s.ang1, ang2: s.ang2, ang3: s.ang3, ratios: `${numText(s.a2 / s.a1)}; ${numText(s.a3 / s.a1)}`, size: s.a1, origin: "ellipsoid", ...where };
}

/** «Вручную», «Из модели «‹имя›», структура 2», «Из эллипсоида вариографии». */
export function originText(o: TrendOrigin, modelName?: (id: number) => string | null): string {
  if (o.type === "ellipsoid") return "Из эллипсоида модели вариограммы";
  if (o.type !== "model") return "Вручную";
  const s = o.structure ? `, структура ${o.structure}` : "";
  if (o.model_id === null || o.model_id === undefined) return `Из модели вариограммы (модель удалена)${s}`;
  const name = modelName?.(o.model_id);
  return `Из модели ${name ? `«${name}»` : `№${o.model_id}`}${s}`;
}

// ---- the view key `ellipsoids` (ADR 0022 §9–10; core `viewstate/keys.py`) ----

export const ELLIPSOIDS_KEY = "ellipsoids";
export const MAX_ELLIPSOIDS_SHOWN = 50;

export interface EllipsoidEntry {
  object: number;
  visible: boolean;
}

/** `[{object, visible}]` as the core reads it: junk dropped, `visible` true unless false, a repeat — the first, ≤ 50 last. */
export function ellipsoidEntries(value: unknown): EllipsoidEntry[] {
  if (!Array.isArray(value)) return [];
  const out: EllipsoidEntry[] = [];
  const seen = new Set<number>();
  for (const v of value) {
    const d = v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
    const id = d?.object;
    if (typeof id !== "number" || !Number.isInteger(id) || id <= 0 || seen.has(id)) continue;
    seen.add(id);
    out.push({ object: id, visible: typeof d?.visible === "boolean" ? d.visible : true });
  }
  return out.slice(-MAX_ELLIPSOIDS_SHOWN);
}

export const shownObjects = (value: unknown): number[] => ellipsoidEntries(value).flatMap((e) => (e.visible ? [e.object] : []));

/** Shows or hides an ellipsoid; a new one goes last, past the limit the oldest hidden ones go first. */
export function showObject(object: number, visible: boolean): (value: unknown) => EllipsoidEntry[] {
  return (value) => {
    const list = ellipsoidEntries(value);
    const i = list.findIndex((e) => e.object === object);
    if (i >= 0) list[i] = { object, visible };
    else list.push({ object, visible });
    while (list.length > MAX_ELLIPSOIDS_SHOWN) {
      const hidden = list.findIndex((e) => !e.visible && e.object !== object);
      list.splice(hidden >= 0 ? hidden : 0, 1);
    }
    return list;
  };
}

export const dropObject =
  (object: number) =>
  (value: unknown): EllipsoidEntry[] =>
    ellipsoidEntries(value).filter((e) => e.object !== object);
