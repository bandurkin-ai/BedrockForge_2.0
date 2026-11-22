/**
 * The state of the windows «Вариография» (ADR 0022 §15): the data of the calculation (set, table, field, domain,
 * weights, transform — the form `DataForm`), the parameters of 8.6.4 as typed text (filled from `GET /defaults`),
 * the bodies of `experimental` / `preview` / `map` built from them (docs/progress/e16-api.md), and the store of the
 * working model of every open window (`variographyStore`): the model of the table, the stored model it came from,
 * the key of the last calculation. The values are checked here against the ranges of 8.6.4 (the mirror of
 * `geostat/variogram/spec.py` RANGES) — a value out of range never reaches the core. No React.
 */
import { Store } from "../../shell/store";
import { parseNumber } from "../composites/params";
import type { CalcResult, DataBody, DefaultsAnswer, FitWeights, ModelRow } from "./api";
import type { MapCalc } from "./mapCalc";
import { EMPTY_MODEL, type Structure, type VModel } from "./model";

// ---- the data ----

export type WeightsChoice = { kind: "auto" } | { kind: "none" } | { kind: "field"; field: string };

export interface DataState {
  setId: number | null;
  /** the key of the interval table (the id as text) or, from the assistant, its name until the list is read */
  table: string | null;
  field: string | null;
  /** null — every point; `table` "" — the table of the data */
  domain: { table: string; field: string; values: string[] } | null;
  weights: WeightsChoice;
  transform: "none" | "nscore";
}

export const DEFAULT_DATA: DataState = { setId: null, table: null, field: null, domain: null, weights: { kind: "auto" }, transform: "none" };

export const DOMAIN_VALUES_MAX = 1000;

/** The data part of a body, or why it cannot be built (Russian, for the form and the assistant). */
export function dataBody(d: DataState): { body: DataBody | null; reason: string | null } {
  const fail = (reason: string) => ({ body: null, reason });
  if (d.setId === null) return fail("Выберите набор скважин");
  if (!d.table) return fail("Выберите таблицу проб или композитов");
  if (!d.field?.trim()) return fail("Выберите числовое поле");
  let domain: DataBody["domain"] = null;
  if (d.domain) {
    if (!d.domain.field.trim()) return fail("Домен: выберите поле домена");
    const values = [...new Set(d.domain.values.map((v) => v.trim()).filter(Boolean))];
    if (!values.length) return fail("Домен: отметьте одно или несколько значений");
    if (values.length > DOMAIN_VALUES_MAX) return fail(`Домен: не больше ${DOMAIN_VALUES_MAX} значений`);
    domain = { ...(d.domain.table && d.domain.table !== d.table ? { table: d.domain.table } : {}), field: d.domain.field.trim(), values };
  }
  const weights = d.weights.kind === "auto" ? null : d.weights.kind === "none" ? false : d.weights.field;
  if (d.weights.kind === "field" && !d.weights.field.trim()) return fail("Веса: выберите поле весов");
  return { body: { set_id: d.setId, table: d.table, field: d.field.trim(), domain, weights, transform: d.transform }, reason: null };
}

/** The data of a command (`set_id, table, field, domain?, domain_table?, domain_values?, weights?, transform?`). */
export function dataFromArgs(a: Record<string, unknown>): DataState {
  const s = (k: string) => (typeof a[k] === "string" && (a[k] as string).trim() ? (a[k] as string).trim() : null);
  const values = s("domain_values")?.split(";").map((v) => v.trim()).filter(Boolean) ?? [];
  const w = s("weights");
  const weights: WeightsChoice = a.weights === false || (w && /^(нет|none|false)$/i.test(w)) ? { kind: "none" } : w ? { kind: "field", field: w } : { kind: "auto" };
  return {
    setId: typeof a.set_id === "number" ? a.set_id : null,
    table: s("table") ?? (typeof a.table === "number" ? String(a.table) : null),
    field: s("field"),
    domain: s("domain") ? { table: s("domain_table") ?? "", field: s("domain")!, values } : null,
    weights,
    transform: a.transform === "nscore" ? "nscore" : "none",
  };
}

// ---- the parameters of 8.6.4 ----

export interface ParamRange {
  label: string;
  lo: number;
  hi: number;
  loOpen?: boolean;
  integer?: boolean;
  /** ∞ allowed */
  inf?: boolean;
  unit?: string;
}

const LEN = 100_000;
/** The mirror of `spec.py` RANGES (ТЗ 8.6.4; e16-api.md «Диапазоны»). */
export const RANGES: Record<string, ParamRange> = {
  lag: { label: "Лаг L", lo: 0, hi: LEN, loOpen: true, unit: "м" },
  nlag: { label: "Число лагов", lo: 1, hi: 100, integer: true },
  lag_tol: { label: "Допуск лага", lo: 0, hi: LEN, unit: "м" },
  az_tol: { label: "Допуск азимута", lo: 0, hi: 90, loOpen: true, unit: "°" },
  dip_tol: { label: "Допуск погружения", lo: 0, hi: 90, loOpen: true, unit: "°" },
  band_h: { label: "Горизонтальная полоса", lo: 0, hi: LEN, loOpen: true, inf: true, unit: "м" },
  band_v: { label: "Вертикальная полоса", lo: 0, hi: LEN, loOpen: true, inf: true, unit: "м" },
  min_pairs: { label: "Наименьшее число пар", lo: 1, hi: 1000, integer: true },
  dh_lag: { label: "Лаг вдоль скважин", lo: 0, hi: LEN, loOpen: true, unit: "м" },
  dh_nlag: { label: "Число лагов вдоль скважин", lo: 1, hi: 100, integer: true },
  dh_lag_tol: { label: "Допуск лага вдоль скважин", lo: 0, hi: LEN, unit: "м" },
  seed: { label: "Зерно выборки", lo: 0, hi: 2 ** 63 - 1, integer: true },
  ang1: { label: "Азимут ang1", lo: 0, hi: 360, unit: "°" },
  ang2: { label: "Погружение ang2", lo: -90, hi: 90, unit: "°" },
  ang3: { label: "Поворот ang3", lo: -180, hi: 180, unit: "°" },
  "fan.n": { label: "Число направлений веера", lo: 4, hi: 72, integer: true },
  "fan.tol": { label: "Допуск направления в плоскости", lo: 0, hi: 90, loOpen: true, unit: "°" },
  "fan.q": { label: "Уровень дальности розы q", lo: 0.5, hi: 1.2 },
  "map.nu": { label: "Ячеек карты по первой оси", lo: 1, hi: 100, integer: true },
  "map.nv": { label: "Ячеек карты по второй оси", lo: 1, hi: 100, integer: true },
  "map.cell": { label: "Ячейка карты", lo: 0, hi: LEN, loOpen: true, unit: "м" },
  "map.thickness": { label: "Толщина слоя карты", lo: 0, hi: LEN, loOpen: true, inf: true, unit: "м" },
  "map.min_pairs": { label: "Наименьшее число пар в ячейке", lo: 1, hi: 1000, integer: true },
};

/** The keys of the form in the order of the left panel; `downhole` is a flag. */
export const CALC_KEYS = ["lag", "nlag", "lag_tol", "az_tol", "dip_tol", "band_h", "band_v", "min_pairs", "dh_lag", "dh_nlag", "dh_lag_tol", "seed"] as const;
export const MAP_KEYS = ["fan.n", "fan.tol", "fan.q", "map.nu", "map.nv", "map.cell", "map.thickness", "map.min_pairs"] as const;

/** Typed text of every key («» — the default of the core) and the flag `downhole`. */
export type CalcValues = Record<string, string> & { downhole?: string };

const text = (v: unknown): string => (typeof v === "number" && Number.isFinite(v) ? String(v).replace(".", ",") : v === "inf" ? "∞" : "");

/** The text of the form from `defaults` of `GET /defaults` (flat keys and `fan`, `map`). */
export function valuesFromDefaults(d: DefaultsAnswer["defaults"]): CalcValues {
  const out: CalcValues = {};
  for (const k of CALC_KEYS) out[k] = text(d[k]);
  for (const k of MAP_KEYS) {
    const [group, key] = k.split(".");
    const g = d[group];
    out[k] = text(g && typeof g === "object" ? (g as Record<string, unknown>)[key] : undefined);
  }
  out.downhole = d.downhole === false ? "false" : "true";
  return out;
}

/** The values of a command: numbers and texts by the same keys (`fan_n` → `fan.n` as well). */
export function valuesFromArgs(a: Record<string, unknown>, base: CalcValues = {}): CalcValues {
  const out: CalcValues = { ...base };
  for (const k of [...CALC_KEYS, ...MAP_KEYS]) {
    const v = a[k] ?? a[k.replace(".", "_")];
    if (typeof v === "number" || typeof v === "string") out[k] = typeof v === "number" ? text(v) : v;
  }
  if (typeof a.downhole === "boolean") out.downhole = String(a.downhole);
  return out;
}

const fmt = (x: number) => String(x).replace(".", ",");
export function spanText(r: ParamRange): string {
  const lo = r.loOpen ? `больше ${fmt(r.lo)}` : `от ${fmt(r.lo)}`;
  return `${lo} до ${fmt(r.hi)}${r.unit ? (r.unit === "°" ? "°" : ` ${r.unit}`) : ""}${r.inf ? " или ∞" : ""}`;
}

/** One value: «» — null (the default); a number in its range; ∞ — "inf" where allowed. */
export function checkValue(key: string, raw: string | undefined): { ok: true; value: number | "inf" | null } | { ok: false; error: string } {
  const r = RANGES[key];
  const t = (raw ?? "").trim();
  if (!t) return { ok: true, value: null };
  if (r.inf && /^(∞|inf)$/i.test(t)) return { ok: true, value: "inf" };
  const v = parseNumber(t);
  if (v === null) return { ok: false, error: `${r.label}: нужно число${r.inf ? " или ∞" : ""}` };
  if (r.integer && !Number.isInteger(v)) return { ok: false, error: `${r.label}: нужно целое число ${spanText(r)}` };
  if (!(r.loOpen ? v > r.lo : v >= r.lo) || !(v <= r.hi)) return { ok: false, error: `${r.label}: значение вне диапазона ${spanText(r)}` };
  return { ok: true, value: v + 0 };
}

export interface CheckedValues {
  values: Record<string, number | "inf" | null>;
  errors: Record<string, string>;
}

/** Every key of `keys`; then the tolerances against their lag (0–L, after L is known; «» — L / 2 of the core). */
export function checkValues(values: CalcValues, keys: readonly string[] = [...CALC_KEYS, ...MAP_KEYS]): CheckedValues {
  const out: CheckedValues = { values: {}, errors: {} };
  for (const k of keys) {
    const r = checkValue(k, values[k]);
    if (r.ok) out.values[k] = r.value;
    else out.errors[k] = r.error;
  }
  for (const [tol, lag] of [["lag_tol", "lag"], ["dh_lag_tol", "dh_lag"]] as const) {
    const t = out.values[tol];
    const L = out.values[lag];
    if (typeof t === "number" && typeof L === "number" && t > L) out.errors[tol] = `${RANGES[tol].label}: значение больше лага (${fmt(L)} м)`;
  }
  return out;
}

export const firstError = (c: CheckedValues): string | null => Object.values(c.errors)[0] ?? null;

export interface AxesAngles {
  ang1: number;
  ang2: number;
  ang3: number;
}

export interface DirectionSpec {
  az: number;
  plunge: number;
  az_tol?: number;
  dip_tol?: number;
  band_h?: number | "inf";
  band_v?: number | "inf";
}

type Built = { body: Record<string, unknown> | null; reason: string | null; errors: Record<string, string> };

/** Only the given numbers: a key left blank takes the default of the core (e16-api.md). */
function put(body: Record<string, unknown>, values: CheckedValues["values"], keys: readonly string[]): void {
  for (const k of keys) {
    const v = values[k];
    if (v !== null && v !== undefined) body[k] = v;
  }
}

function angles(a: AxesAngles, where: string): { value: AxesAngles | null; error: string | null } {
  const out: Record<string, number> = {};
  for (const k of ["ang1", "ang2", "ang3"] as const) {
    const r = checkValue(k, String(a[k]));
    if (!r.ok) return { value: null, error: `${where}: ${r.error}` };
    out[k] = typeof r.value === "number" ? r.value : 0;
  }
  if (out.ang1 === 360) out.ang1 = 0;
  if (out.ang3 === -180) out.ang3 = 180;
  return { value: out as unknown as AxesAngles, error: null };
}

/** `POST /experimental` and `/preview`: the data, `axes` (the three axes of the model) or `directions` (1–72). */
export function experimentalBody(d: DataState, values: CalcValues, dirs: { axes: AxesAngles } | { directions: DirectionSpec[] }): Built {
  const data = dataBody(d);
  if (!data.body) return { body: null, reason: data.reason, errors: {} };
  const keys = [...CALC_KEYS];
  const c = checkValues(values, keys);
  const bad = firstError(c);
  if (bad) return { body: null, reason: bad, errors: c.errors };
  const body: Record<string, unknown> = { ...data.body };
  if ("axes" in dirs) {
    const a = angles(dirs.axes, "Оси");
    if (!a.value) return { body: null, reason: a.error, errors: {} };
    body.axes = a.value;
  } else {
    if (!dirs.directions.length || dirs.directions.length > 72) return { body: null, reason: "Направлений должно быть от 1 до 72", errors: {} };
    body.directions = dirs.directions.map((x) => ({ ...x }));
  }
  put(body, c.values, keys);
  body.downhole = values.downhole !== "false";
  return { body, reason: null, errors: {} };
}

export type MapWhat = "fan" | "map";

/** `POST /map`: the plane (a1, a2; the normal a3), what (fan with the rose and / or map), the lags, `fan`, `map`. */
export function mapBody(d: DataState, values: CalcValues, plane: AxesAngles, what: readonly MapWhat[] = ["fan", "map"]): Built {
  const data = dataBody(d);
  if (!data.body) return { body: null, reason: data.reason, errors: {} };
  const keys = ["lag", "nlag", "lag_tol", "dip_tol", "band_v", "min_pairs", "seed", ...MAP_KEYS];
  const c = checkValues(values, keys);
  const bad = firstError(c);
  if (bad) return { body: null, reason: bad, errors: c.errors };
  const p = angles(plane, "Плоскость");
  if (!p.value) return { body: null, reason: p.error, errors: {} };
  if (!what.length) return { body: null, reason: "Выберите веер или карту", errors: {} };
  const body: Record<string, unknown> = { ...data.body, plane: p.value, what: (["fan", "map"] as const).filter((w) => what.includes(w)) };
  put(body, c.values, ["lag", "nlag", "lag_tol", "dip_tol", "band_v", "min_pairs", "seed"]);
  for (const group of ["fan", "map"] as const) {
    const sub: Record<string, unknown> = {};
    for (const k of MAP_KEYS.filter((x) => x.startsWith(`${group}.`))) {
      const v = c.values[k];
      if (v !== null && v !== undefined) sub[k.slice(group.length + 1)] = v;
    }
    if (Object.keys(sub).length) body[group] = sub;
  }
  return { body, reason: null, errors: {} };
}

/** `param` of a refusal of the core → the key of the form («directions.0.az» stays as it is). */
export const paramKey = (param: unknown): string | null => (typeof param === "string" && param ? param : null);

// ---- the working models of the windows ----

export type VarioView = "axes" | "downhole" | "fan" | "map" | "nscore";
export const VIEWS: readonly VarioView[] = ["axes", "downhole", "fan", "map", "nscore"];

export interface Work {
  data: DataState;
  values: CalcValues;
  view: VarioView;
  /** the model of the table of the window */
  model: VModel;
  /** the stored model it was read from (or saved as); null — new */
  modelId: number | null;
  /** the key of the last `experimental` (for `fit`, `save`); null — not computed */
  expKey: string | null;
  /** the paths of the parameters fixed in the fit (`c0`, `structures.N.c`, `structures.N.a1…a3`) */
  fix: string[];
  result: CalcResult | null;
  defaults: DefaultsAnswer | null;
  /** changed since it was read or saved */
  dirty: boolean;
  /** the ellipsoid of the model in the main 3D (Д-14); absent — not shown */
  ellipsoid?: EllipsoidShow;
  /** the weights of «Подобрать» (N / h̄ by default) */
  fitWeights?: FitWeights;
  /** the fit the model came from (`fit` of the save); an edit of the model drops it — its residual is of another model */
  fitInfo?: FitInfo | null;
  /** the answer of `/back` for the model (normal scores; `back` of the save); an edit drops it */
  back?: Record<string, unknown> | null;
  /** `job.result` of `variography.map` (the fan, the rose, the map) and the body (JSON) it was computed by */
  mapCalc?: MapCalc | null;
  mapFor?: string | null;
  /** the format of «Экспорт» of the window */
  exchangeFormat?: "json" | "gslib";
}

/** `fit` of a stored model: the answer of `/fit` (e16-api.md «Модели вариограмм»). */
export interface FitInfo {
  weights: FitWeights;
  residual: number | null;
  n: number | null;
  fixed: string[];
  warnings?: string[];
}

/** The ellipsoid of a window in 3D: shown, the structure with the handles (null — of the largest a1), the centre. */
export interface EllipsoidShow {
  visible: boolean;
  structure: number | null;
  /** absolute centre, float64; null — the centre of the extent of the points */
  centre: [number, number, number] | null;
}

export const newWork = (patch: Partial<Work> = {}): Work => ({ data: DEFAULT_DATA, values: {}, view: "axes", model: EMPTY_MODEL, modelId: null, expKey: null, fix: [], result: null, defaults: null, dirty: false, ...patch });

export interface VarioState {
  /** window id → its working state */
  works: Record<string, Work>;
  models: ModelRow[];
  modelsLoaded: boolean;
  /** a handle of the ellipsoid being dragged (the window waits for the release to compute); null — none */
  dragging?: { win: string; kind: string } | null;
}

export const variographyStore = new Store<VarioState>({ works: {}, models: [], modelsLoaded: false, dragging: null });

export const workOf = (win: string): Work | null => variographyStore.get().works[win] ?? null;

export function setWork(win: string, patch: Partial<Work> | ((w: Work) => Partial<Work>)): Work {
  const cur = workOf(win) ?? newWork();
  const next = { ...cur, ...(typeof patch === "function" ? patch(cur) : patch) };
  variographyStore.set((s) => ({ works: { ...s.works, [win]: next } }));
  return next;
}

/** The model of the table changed by hand (a cell, a drag): the window is «dirty», the fit and the back-transform of the old model are dropped. */
export const setModel = (win: string, model: VModel): Work => setWork(win, { model, dirty: true, fitInfo: null, back: null });

export function dropWork(win: string): void {
  variographyStore.set((s) => {
    const works = { ...s.works };
    delete works[win];
    return { works };
  });
}

/** The window holding a stored model. */
export const windowOfModel = (id: number): string | null => Object.entries(variographyStore.get().works).find(([, w]) => w.modelId === id)?.[0] ?? null;

/** After a delete: the windows of that model keep their model as a new one. */
export function forgetModel(id: number): void {
  variographyStore.set((s) => ({
    works: Object.fromEntries(Object.entries(s.works).map(([k, w]) => [k, w.modelId === id ? { ...w, modelId: null, dirty: true } : w])),
    models: s.models.filter((m) => m.id !== id),
  }));
}

export const setModels = (models: ModelRow[]): void => variographyStore.set({ models, modelsLoaded: true });

/** «‹поле› · ‹таблица›» — the default name of a model (e16-api.md). */
export const defaultModelName = (w: Work): string => [w.data.field, w.defaults?.table_name ?? w.result?.table].filter(Boolean).join(" · ");

// ---- the ellipsoid in 3D (Д-14; ADR 0022 §9) ----

export const DEFAULT_ELLIPSOID: EllipsoidShow = { visible: true, structure: null, centre: null };

export const setEllipsoid = (win: string, patch: Partial<EllipsoidShow>): Work => setWork(win, (w) => ({ ellipsoid: { ...DEFAULT_ELLIPSOID, ...w.ellipsoid, ...patch } }));

export const setDragging = (d: VarioState["dragging"]): void => variographyStore.set({ dragging: d });

/** The centre of the ellipsoid of a window: given, else the middle of the extent of the points (`/defaults`); null — unknown. */
export function ellipsoidCentre(w: Work): [number, number, number] | null {
  if (w.ellipsoid?.centre) return w.ellipsoid.centre;
  const e = w.defaults?.extent;
  if (e) return [(e.min[0] + e.max[0]) / 2, (e.min[1] + e.max[1]) / 2, (e.min[2] + e.max[2]) / 2];
  return null;
}

/** The least range of a drag: L / 2 of the last calculation (or of the defaults); 0 — unknown. */
export function leastRange(w: Work): number {
  const lag = Number(w.result?.params.lag ?? w.defaults?.defaults.lag ?? 0);
  return Number.isFinite(lag) && lag > 0 ? lag / 2 : 0;
}

/** The structure with the handles: given (if there is one), else the one of the largest a1 (the first of equal ones). */
export function chosenStructure(w: Pick<Work, "model" | "ellipsoid">): number {
  const list = w.model.structures;
  const s = w.ellipsoid?.structure;
  if (typeof s === "number" && s >= 0 && s < list.length) return s;
  let best = list.length ? 0 : -1;
  list.forEach((x, i) => {
    if (x.a1 > list[best].a1) best = i;
  });
  return best;
}

export type StructurePatch = Partial<Pick<Structure, "ang1" | "ang2" | "ang3" | "a1" | "a2" | "a3">>;

/** The model with the structure `i` changed; its angles — of every structure with «общие оси» (they turn together). */
export function withStructure(m: VModel, i: number, patch: StructurePatch): VModel {
  const angles: StructurePatch = {};
  for (const k of ["ang1", "ang2", "ang3"] as const) if (patch[k] !== undefined) angles[k] = patch[k];
  const structures = m.structures.map((s, k) => (k === i ? { ...s, ...patch } : m.common_axes ? { ...s, ...angles } : s));
  return { ...m, structures };
}

/** The mark `vario:edit` of the frame of an edit (a drag, a cell): `vario:drawn` comes with the frame (`scene.ts`). */
let editAt: number | null = null;
export function markEdit(): void {
  if (typeof performance === "undefined") return;
  editAt = performance.now();
  try {
    performance.mark("vario:edit");
  } catch {
    /* no User Timing: the time is still kept */
  }
}

/** The time of the last edit not drawn yet, taken once (null — none). */
export function takeEdit(): number | null {
  const t = editAt;
  editAt = null;
  return t;
}
