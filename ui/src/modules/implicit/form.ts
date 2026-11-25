/**
 * The form of the wizard «Новая модель» (ТЗ 8.9.4; docs/progress/e19-api.md §3–§4) and its body: the boundary (a box of the extent
 * or a closed wireframe, optionally under a DTM), the step h and the levels L with the number of the cells of the grid, the data of
 * the domains (the set, the table, the field of the codes, D_max, Δ_т), the groups of the lithology (the codes, the order by age:
 * the first is the youngest), the manual layers, the kernel, the smoothing, the trend or the anisotropy by hand, the patches and the
 * post-processing. The typed fields stay text («0,5») and become numbers in `bodyOf`; the ranges are those of the core (`ranges` of
 * `GET /defaults`; `FALLBACK_RANGES` mirror them for the time before the answer — a test compares). A group that is not a domain
 * (a shell, a vein, an expansion — 19.24) is kept as the core gave it. No React.
 */
import { numberText, parseList, parseNumber } from "../composites/params";
import { bad, isIssue, numberIn, type Bounds, type Issue } from "../estimation/formBase";
import type { ClipKind, DefaultsAnswer, GroupMeta, KernelKind, ModelDefaults, Range, SmoothingMode } from "./api";

export type { Issue };

export type BoundaryKind = "box" | "wireframe";
export type TrendMode = "none" | "list" | "manual";
export type ManualRole = "contact" | "inside" | "outside";

export interface GroupForm {
  /** unique in the model; a new group gets max + 1 (the manual layers refer to it) */
  id: number;
  name: string;
  type: string;
  codes: string[];
  /** the group as the core keeps it: what the wizard does not edit (relations, faults, the parameters of a shell) goes back unchanged */
  raw: GroupMeta | null;
}

export interface ManualForm {
  layerId: number | null;
  role: ManualRole;
  /** the id of the group */
  group: number | null;
}

export interface ModelForm {
  name: string;
  boundary: BoundaryKind;
  box: string[];
  wireframeId: number | null;
  dtmId: number | null;
  h: string;
  levels: string;
  setId: number | null;
  table: string | null;
  field: string | null;
  dMax: string;
  step: string;
  groups: GroupForm[];
  manual: ManualForm[];
  kernel: KernelKind;
  kernelRadius: string;
  smoothing: SmoothingMode;
  s: string;
  cond: string;
  trend: TrendMode;
  trendId: number | null;
  ang: [string, string, string];
  /** r2, r3 (r1 = 1) */
  ratios: [string, string];
  nPatch: string;
  nMin: string;
  delta: string;
  vMin: string;
  simplify: boolean;
  clip: ClipKind;
  clipId: number | null;
}

export const EMPTY_FORM: ModelForm = {
  name: "",
  boundary: "box",
  box: ["", "", "", "", "", ""],
  wireframeId: null,
  dtmId: null,
  h: "",
  levels: "",
  setId: null,
  table: null,
  field: null,
  dMax: "",
  step: "",
  groups: [],
  manual: [],
  kernel: "linear",
  kernelRadius: "",
  smoothing: "manual",
  s: "",
  cond: "",
  trend: "none",
  trendId: null,
  ang: ["0", "0", "0"],
  ratios: ["1", "1"],
  nPatch: "",
  nMin: "",
  delta: "",
  vMin: "",
  simplify: false,
  clip: "boundary",
  clipId: null,
};

export const STEP_TITLES = ["Граница и шаг", "Домены литологии", "Интерполяция", "Итог"] as const;
export const LAST_STEP = STEP_TITLES.length;

export const DEFAULT_NAME = "Неявная модель";
export const NAME_MAX = 200;

/** The step a refusal is about (the path of the body); 0 — none (the general words). */
export function stepOfParam(param: string | null): number {
  if (!param) return 0;
  const head = param.split(".")[0];
  if (["name", "boundary", "h", "levels", "dtm_id"].includes(head)) return 1;
  if (["data", "groups", "manual"].includes(head)) return 2;
  if (["kernel", "kernel_radius", "smoothing", "trend_id", "anisotropy", "pou"].includes(head)) return 3;
  if (["post", "expected_version"].includes(head)) return 4;
  return 0;
}

// ---- the ranges (the mirror of `spec_parts.RANGES`; the answer of `GET /defaults` is the source) ----

const R = (lo: number | null, hi: number | null, extra: Partial<Range> = {}): Range => ({ lo, hi, lo_open: false, integer: false, unit: "", ...extra });

export const FALLBACK_RANGES: Record<string, Range> = {
  h: R(0.1, 100, { unit: "м" }),
  levels: R(0, 4, { integer: true }),
  n_patch: R(300, 4000, { integer: true }),
  n_min: R(20, 500, { integer: true }),
  delta: R(0.1, 1),
  d_max: R(1, 500, { unit: "м" }),
  step: R(0.5, 50, { unit: "м" }),
  s: R(0, 10),
  cond_target: R(1e4, 1e12),
  kernel_radius: R(0, 100000, { lo_open: true, unit: "м" }),
  ang1: R(0, 360, { unit: "°" }),
  ang2: R(-90, 90, { unit: "°" }),
  ang3: R(-180, 180, { unit: "°" }),
  ratio: R(0, 1000000, { lo_open: true }),
  v_min: R(0, null, { unit: "м³" }),
};

/** The ranges the form checks against: those of the core when it has answered, else the mirror. */
export const rangesOf = (d: DefaultsAnswer | null): Record<string, Range> => ({ ...FALLBACK_RANGES, ...(d?.ranges ?? {}) });

const boundsOf = (ranges: Record<string, Range>, key: string): Bounds => {
  const r = ranges[key] ?? FALLBACK_RANGES[key];
  return { lo: r.lo, hi: r.hi, integer: r.integer, open: r.lo_open, ...(r.unit ? { unit: r.unit } : {}) };
};

// ---- the extent, the step and the cells of the grid (8.9.3, 8.9.4) ----

export type Extent = [number, number, number, number, number, number];

/** The extent [xmin, ymin, zmin, xmax, ymax, zmax] of the boundary: the typed box, or the extent of the wireframe; null — not known. */
export function extentOf(f: ModelForm, wireframeExtent: (id: number) => readonly (number | null)[] | null): Extent | null {
  if (f.boundary === "box") {
    const v = f.box.map(parseNumber);
    return v.every((x): x is number => x !== null) && v[0] < v[3] && v[1] < v[4] && v[2] < v[5] ? (v as Extent) : null;
  }
  const e = f.wireframeId === null ? null : wireframeExtent(f.wireframeId);
  return e && e.length >= 6 && e.slice(0, 6).every((x): x is number => typeof x === "number" && Number.isFinite(x)) ? ([...e.slice(0, 6)] as Extent) : null;
}

export const diagonalOf = (e: Extent): number => Math.hypot(e[3] - e[0], e[4] - e[1], e[5] - e[2]);

/** The step of the core by default: the diagonal of the extent / 200, in the range of h (`spec.resolve_h`). */
export function defaultStep(e: Extent, d: Pick<ModelDefaults, "h_divisor">, ranges: Record<string, Range>): number {
  const r = ranges.h ?? FALLBACK_RANGES.h;
  const h = diagonalOf(e) / d.h_divisor;
  return Math.min(r.hi ?? Infinity, Math.max(r.lo ?? 0, h));
}

export interface GridInfo {
  /** the step used: the typed one or the default */
  h: number;
  /** false — the step is the default of the core */
  typed: boolean;
  levels: number;
  /** the cells of the step h along the axes: a multiple of 2^L covering the extent */
  n: [number, number, number];
  cells: number;
  /** a count along an axis above 2^20: the core refuses (`bad_param h`) */
  tooMany: boolean;
}

export const AXIS_MAX = 2 ** 20;

/** The grid of the model by the form: the number of the cells along the axes and in all (null — the extent or the step is not known yet). */
export function gridOf(f: ModelForm, e: Extent | null, d: Pick<ModelDefaults, "h_divisor" | "levels">, ranges: Record<string, Range>): GridInfo | null {
  if (!e) return null;
  const typedH = parseNumber(f.h);
  const h = typedH ?? defaultStep(e, d, ranges);
  const lv = f.levels.trim() === "" ? d.levels : parseNumber(f.levels);
  if (!(h > 0) || lv === null || !Number.isInteger(lv) || lv < 0 || lv > 8) return null;
  const q = 2 ** lv;
  const n = [0, 1, 2].map((i) => Math.ceil(Math.ceil((e[i + 3] - e[i]) / h) / q) * q) as [number, number, number];
  return { h, typed: typedH !== null, levels: lv, n, cells: n[0] * n[1] * n[2], tooMany: n.some((x) => x > AXIS_MAX) };
}

// ---- the groups ----

/** The next id of a group: max + 1. */
export const nextGroupId = (groups: readonly GroupForm[]): number => groups.reduce((m, g) => Math.max(m, g.id), 0) + 1;

/** A new domain group, last (the oldest). */
export function newDomain(groups: readonly GroupForm[], name: string, codes: string[] = []): GroupForm {
  return { id: nextGroupId(groups), name, type: "domain", codes, raw: null };
}

/** The first free name «Домен N». */
export function freeName(groups: readonly GroupForm[], base = "Домен"): string {
  const names = new Set(groups.map((g) => g.name));
  for (let i = groups.length + 1; ; i++) if (!names.has(`${base} ${i}`)) return `${base} ${i}`;
}

/** The codes of the field the groups hold, in the order of the groups. */
export const usedCodes = (groups: readonly GroupForm[]): Set<string> => new Set(groups.flatMap((g) => g.codes));

/** Moves the group `i` by `d` (−1 — younger, +1 — older). */
export function moveGroup(groups: readonly GroupForm[], i: number, d: -1 | 1): GroupForm[] {
  const j = i + d;
  if (j < 0 || j >= groups.length) return [...groups];
  const out = [...groups];
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

/** The groups that the wizard edits (domains) and the ones it only carries (shells, veins, expansions, faults). */
export const isDomain = (g: GroupForm): boolean => g.type === "domain";
export const TYPE_TITLES: Record<string, string> = { domain: "домен", shell: "оболочка", vein: "жила", expand: "расширение", fault: "разлом", vein_system: "система жил" };

// ---- the body (§3) ----

export type Built = { ok: true; body: Record<string, unknown> } | { ok: false; issue: Issue };

const fail = (issue: Issue): { ok: false; issue: Issue } => ({ ok: false, issue });

/** A typed number or empty (null); a wrong one — the words under the field. */
function optional(text: string, key: string, label: string, param: string, ranges: Record<string, Range>): number | null | Issue {
  if (text.trim() === "") return null;
  return numberIn(text, boundsOf(ranges, key), label, param);
}

/**
 * The body of `POST /models` / `PUT /models/{id}` from the form: the first wrong thing becomes the words under its field (the paths
 * are those of the core's `param`); what the core decides itself (a wireframe that is open, a field that is not in the table, the
 * number of the points) is left to it.
 */
export function bodyOf(f: ModelForm, d: DefaultsAnswer | null): Built {
  const ranges = rangesOf(d);
  const body: Record<string, unknown> = {};
  const name = f.name.trim();
  if (name.length > NAME_MAX) return fail(bad("name", `Название длиннее ${NAME_MAX} знаков.`, "Сократите название модели."));
  if (name) body.name = name;

  // the boundary
  const boundary: Record<string, unknown> = {};
  if (f.boundary === "box") {
    const box: number[] = [];
    for (let i = 0; i < 6; i++) {
      const v = parseNumber(f.box[i] ?? "");
      if (v === null) return fail(bad(`boundary.box.${i}`, `Охват: ${BOX_LABELS[i]} — не число.`, "Введите шесть чисел: от X до Z минимум и максимум, например 0; 0; −300; 1000; 800; 100."));
      box.push(v);
    }
    for (let i = 0; i < 3; i++) if (box[i] >= box[i + 3]) return fail(bad(`boundary.box.${i + 3}`, `Охват: ${AXES[i]} минимум не меньше максимума.`, "Максимум должен быть больше минимума.", "boundary_empty"));
    boundary.box = box;
  } else {
    if (f.wireframeId === null) return fail(bad("boundary.wireframe_id", "Не выбран каркас границы.", "Выберите замкнутый каркас или задайте охват."));
    boundary.wireframe_id = f.wireframeId;
  }
  if (f.dtmId !== null) boundary.dtm_id = f.dtmId;
  body.boundary = boundary;

  // the step and the levels
  const h = optional(f.h, "h", "Шаг h", "h", ranges);
  if (isIssue(h)) return fail(h);
  if (h !== null) body.h = h;
  const lv = optional(f.levels, "levels", "Уровни L", "levels", ranges);
  if (isIssue(lv)) return fail(lv);
  if (lv !== null) body.levels = lv;

  // the data of the domains and the groups
  const hasCodes = f.groups.some((g) => g.codes.length > 0);
  const partial = [f.setId !== null, !!f.table, !!f.field];
  if (partial.some(Boolean) && !partial.every(Boolean)) return fail(bad(f.setId === null ? "data.set_id" : !f.table ? "data.table" : "data.field", "Данные доменов заданы не полностью.", "Выберите набор скважин, таблицу и поле кода — или уберите набор."));
  if (hasCodes && f.setId === null) return fail(bad("data.set_id", "У групп есть коды, а таблица кодов не выбрана.", "Выберите набор скважин, интервальную таблицу и поле кода."));
  if (f.setId !== null && f.table && f.field) {
    const dMax = optional(f.dMax, "d_max", "D_max", "data.d_max", ranges);
    if (isIssue(dMax)) return fail(dMax);
    const step = optional(f.step, "step", "Шаг точек Δ_т", "data.step", ranges);
    if (isIssue(step)) return fail(step);
    body.data = { set_id: f.setId, table: f.table, field: f.field, ...(dMax !== null ? { d_max: dMax } : {}), ...(step !== null ? { step } : {}) };
  } else body.data = null;

  const limits = d?.params.limits ?? { groups: 50, codes: 1000, manual: 100 };
  if (f.groups.length > limits.groups) return fail(bad("groups", `Групп больше ${limits.groups}.`, "Уберите лишние группы или объедините их коды."));
  const seen = new Map<string, number>();
  const names = new Set<string>();
  const groups: Record<string, unknown>[] = [];
  for (let i = 0; i < f.groups.length; i++) {
    const g = f.groups[i];
    const gn = g.name.trim();
    if (!gn) return fail(bad(`groups.${i}.name`, `Группа ${i + 1}: нет названия.`, "Назовите группу, например по породе."));
    if (names.has(gn)) return fail(bad(`groups.${i}.name`, `Название «${gn}» повторяется.`, "Названия групп в модели не повторяются."));
    names.add(gn);
    if (g.codes.length > limits.codes) return fail(bad(`groups.${i}.codes`, `У группы «${gn}» кодов больше ${limits.codes}.`, "Разделите группу."));
    for (let j = 0; j < g.codes.length; j++) {
      const at = seen.get(g.codes[j]);
      if (at !== undefined) return fail(bad(`groups.${i}.codes.${j}`, `Код «${g.codes[j]}» уже есть в группе «${f.groups[at].name}».`, "Код входит в одну группу модели."));
      seen.set(g.codes[j], i);
    }
    if (isDomain(g) && !g.codes.length && !f.manual.some((m) => m.group === g.id)) return fail(bad(`groups.${i}.codes`, `У группы «${gn}» нет данных.`, "Отметьте коды группы или добавьте ручной слой."));
    groups.push({ ...(g.raw ?? {}), id: g.id, name: gn, type: g.type, codes: g.codes });
  }
  body.groups = groups;

  if (f.manual.length > limits.manual) return fail(bad("manual", `Ручных слоёв больше ${limits.manual}.`, "Уберите лишние слои."));
  const manual: Record<string, unknown>[] = [];
  for (let i = 0; i < f.manual.length; i++) {
    const m = f.manual[i];
    if (m.layerId === null) return fail(bad(`manual.${i}.layer_id`, `Ручные данные ${i + 1}: не выбран слой.`, "Выберите слой линий (редактор стрингов)."));
    if (m.group === null || !f.groups.some((g) => g.id === m.group)) return fail(bad(`manual.${i}.group`, `Ручные данные ${i + 1}: не выбрана группа.`, "Выберите группу модели, к которой относится слой."));
    manual.push({ layer_id: m.layerId, role: m.role, group: m.group });
  }
  body.manual = manual;

  // the interpolation
  body.kernel = f.kernel;
  if (f.kernel === "wendland") {
    const a = optional(f.kernelRadius, "kernel_radius", "Радиус ядра a", "kernel_radius", ranges);
    if (isIssue(a)) return fail(a);
    if (a !== null) body.kernel_radius = a;
  }
  if (f.smoothing === "manual") {
    const s = optional(f.s, "s", "Сглаживание s", "smoothing.s", ranges);
    if (isIssue(s)) return fail(s);
    body.smoothing = { mode: "manual", ...(s !== null ? { s } : {}) };
  } else {
    const c = optional(f.cond, "cond_target", "Целевое число обусловленности", "smoothing.cond_target", ranges);
    if (isIssue(c)) return fail(c);
    const s = optional(f.s, "s", "Начальное сглаживание s", "smoothing.s", ranges);
    if (isIssue(s)) return fail(s);
    body.smoothing = { mode: "auto", ...(s !== null ? { s } : {}), ...(c !== null ? { cond_target: c } : {}) };
  }
  if (f.trend === "list") {
    if (f.trendId === null) return fail(bad("trend_id", "Не выбран тренд.", "Выберите тренд из списка или задайте анизотропию вручную."));
    body.trend_id = f.trendId;
  } else if (f.trend === "manual") {
    const ang: number[] = [];
    for (let i = 0; i < 3; i++) {
      const v = numberIn(f.ang[i], boundsOf(ranges, `ang${i + 1}`), `Угол ${i + 1}`, `anisotropy.ang${i + 1}`);
      if (isIssue(v)) return fail(v);
      ang.push(v);
    }
    const ratios: number[] = [1];
    for (let i = 0; i < 2; i++) {
      const v = numberIn(f.ratios[i], boundsOf(ranges, "ratio"), `Отношение осей r${i + 2}`, `anisotropy.ratios.${i + 1}`);
      if (isIssue(v)) return fail(v);
      ratios.push(v);
    }
    body.anisotropy = { ang1: ang[0], ang2: ang[1], ang3: ang[2], ratios };
  }

  // the patches and the post-processing
  const pou: Record<string, number> = {};
  for (const [key, typed, label, param] of [
    ["n_patch", f.nPatch, "N_уч", "pou.n_patch"],
    ["n_min", f.nMin, "N_min", "pou.n_min"],
    ["delta", f.delta, "δ", "pou.delta"],
  ] as const) {
    const v = optional(typed, key, label, param, ranges);
    if (isIssue(v)) return fail(v);
    if (v !== null) pou[key] = v;
  }
  const nMin = pou.n_min ?? d?.params.n_min ?? 100;
  const nPatch = pou.n_patch ?? d?.params.n_patch ?? 1000;
  if (nMin > nPatch) return fail(bad("pou.n_min", "N_min больше N_уч.", "Наименьшее число точек участка не больше наибольшего."));
  if (Object.keys(pou).length) body.pou = pou;
  const vMin = optional(f.vMin, "v_min", "Наименьший объём V_min", "post.v_min", ranges);
  if (isIssue(vMin)) return fail(vMin);
  if ((f.clip === "wireframe" || f.clip === "dtm") && f.clipId === null) return fail(bad("post.clip_id", `Не выбран ${f.clip === "dtm" ? "ЦТМ" : "каркас"} обрезки.`, "Выберите объект обрезки или другой способ."));
  body.post = { ...(vMin !== null ? { v_min: vMin } : {}), simplify: f.simplify, clip: f.clip, ...(f.clip === "wireframe" || f.clip === "dtm" ? { clip_id: f.clipId } : {}) };
  return { ok: true, body };
}

export const AXES = ["X", "Y", "Z"] as const;
export const BOX_LABELS = ["X мин", "Y мин", "Z мин", "X макс", "Y макс", "Z макс"] as const;

// ---- the form of a stored model (`meta` is `spec.canonical()` + the results) ----

const text = (v: unknown): string => (typeof v === "number" && Number.isFinite(v) ? numberText(v) : "");
const rec = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

export function formOfMeta(name: string, meta: Record<string, unknown>): ModelForm {
  const b = rec(meta.boundary);
  const box = Array.isArray(b.box) && b.box.length === 6 ? b.box : null;
  const sm = rec(meta.smoothing);
  const an = meta.anisotropy ? rec(meta.anisotropy) : null;
  const pou = rec(meta.pou);
  const data = meta.data ? rec(meta.data) : null;
  const post = rec(meta.post);
  const ratios = Array.isArray(an?.ratios) ? an!.ratios : [];
  const groups: GroupForm[] = (Array.isArray(meta.groups) ? meta.groups : []).map((x, i) => {
    const g = rec(x);
    const raw: GroupMeta = {
      id: typeof g.id === "number" ? g.id : i + 1,
      name: typeof g.name === "string" ? g.name : "",
      type: typeof g.type === "string" ? g.type : "domain",
      codes: Array.isArray(g.codes) ? g.codes.map((c) => String(c)) : [],
      relation: typeof g.relation === "string" ? g.relation : "conformable",
      faults: Array.isArray(g.faults) ? g.faults.filter((n): n is number => typeof n === "number") : [],
      params: rec(g.params),
    };
    return { id: raw.id ?? i + 1, name: raw.name, type: raw.type, codes: raw.codes, raw };
  });
  const clip = post.clip === "none" || post.clip === "wireframe" || post.clip === "dtm" ? post.clip : "boundary";
  const kernel = meta.kernel === "cubic" || meta.kernel === "wendland" ? meta.kernel : "linear";
  return {
    ...EMPTY_FORM,
    name,
    boundary: box ? "box" : "wireframe",
    box: box ? box.map(text) : EMPTY_FORM.box,
    wireframeId: typeof b.wireframe_id === "number" ? b.wireframe_id : null,
    dtmId: typeof b.dtm_id === "number" ? b.dtm_id : null,
    h: text(meta.h),
    levels: text(meta.levels),
    setId: typeof data?.set_id === "number" ? data.set_id : null,
    table: data && data.table !== null && data.table !== undefined ? String(data.table) : null,
    field: typeof data?.field === "string" ? data.field : null,
    dMax: text(data?.d_max),
    step: text(data?.step),
    groups,
    manual: (Array.isArray(meta.manual) ? meta.manual : []).map((x) => {
      const m = rec(x);
      return { layerId: typeof m.layer_id === "number" ? m.layer_id : null, role: m.role === "inside" || m.role === "outside" ? m.role : "contact", group: typeof m.group === "number" ? m.group : null };
    }),
    kernel,
    kernelRadius: text(meta.kernel_radius),
    smoothing: sm.mode === "auto" ? "auto" : "manual",
    s: text(sm.s),
    cond: text(sm.cond_target),
    trend: typeof meta.trend_id === "number" ? "list" : an ? "manual" : "none",
    trendId: typeof meta.trend_id === "number" ? meta.trend_id : null,
    ang: an ? [text(an.ang1) || "0", text(an.ang2) || "0", text(an.ang3) || "0"] : EMPTY_FORM.ang,
    ratios: an ? [text(ratios[1]) || "1", text(ratios[2]) || "1"] : EMPTY_FORM.ratios,
    nPatch: text(pou.n_patch),
    nMin: text(pou.n_min),
    delta: text(pou.delta),
    vMin: post.v_min === 0 ? "" : text(post.v_min),
    simplify: post.simplify === true,
    clip,
    clipId: typeof post.clip_id === "number" ? post.clip_id : null,
  };
}

// ---- the arguments of the command `implicit.new` ----

const BOX_HINT = "Шесть чисел через «;»: X мин; Y мин; Z мин; X макс; Y макс; Z макс";

/** `box` of a command: «0; 0; −300; 1000; 800; 100» → six numbers, or the words of the refusal. */
export function parseBox(textBox: string): number[] | Issue {
  const v = parseList(textBox.replace(/−/g, "-"));
  if (!v || v.length !== 6) return bad("boundary.box", "Охват: нужно шесть чисел.", BOX_HINT);
  for (let i = 0; i < 3; i++) if (v[i] >= v[i + 3]) return bad(`boundary.box.${i + 3}`, `Охват: ${AXES[i]} минимум не меньше максимума.`, "Максимум должен быть больше минимума.", "boundary_empty");
  return v;
}

/** The form of `implicit.new {name?, box?, wireframe_id?, h?}` over the base form; the refusal — the words of the first wrong argument. */
export function formOfArgs(a: Record<string, unknown>, base: ModelForm): ModelForm | Issue {
  const f: ModelForm = { ...base };
  if (typeof a.name === "string") f.name = a.name;
  if (typeof a.box === "string" && a.box.trim()) {
    const v = parseBox(a.box);
    if (isIssue(v)) return v;
    f.boundary = "box";
    f.box = v.map(text);
    f.wireframeId = null;
  }
  if (typeof a.wireframe_id === "number") {
    f.boundary = "wireframe";
    f.wireframeId = a.wireframe_id;
  }
  if (a.h !== undefined) f.h = typeof a.h === "number" ? text(a.h) : String(a.h);
  return f;
}

/** The body of `POST /models` from the arguments of a command (the form of the arguments over the empty one). */
export const boundaryGiven = (a: Record<string, unknown>): boolean => (typeof a.box === "string" && a.box.trim() !== "") || typeof a.wireframe_id === "number";
