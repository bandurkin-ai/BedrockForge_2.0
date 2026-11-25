/**
 * Steps 4 and 5 of the wizard «Оценка содержаний» as a form (ТЗ 8.8.2, 8.8.4; Д-29; docs/progress/e18-api.md §3): the ellipsoid of
 * the search (from the model of variogram, from a trend, by hand), the passes (≤ 5: factor, n_min, n_max, n_hole, n_oct, k_oct,
 * n_holes_min and the rule of the gold-grade samples `hy {t, q, mode}`, off by default), the output fields (the prefix and the keys:
 * STATUS is always written). A pass or a field the person did not touch stays `null` — the core gives its defaults (`defaults`),
 * the wizard shows them (`params.search.passes`, `params.outputs.fields`). The numbers are typed text («0,5»); the words of a wrong
 * value are those of the core with the place of the body. No React.
 */
import { numberText } from "../composites/params";
import { obj, type MethodKind } from "./api";
import { bad, isIssue, numberIn, PASSES_MAX, PREFIX_MAX, RANGES, type Issue } from "./formBase";

export type Tri = [string, string, string];
export type HyMode = "drop" | "cap";

export interface HyForm {
  on: boolean;
  t: string;
  /** the share of the radius of the pass (0; 1] */
  q: string;
  mode: HyMode;
}

export interface PassForm {
  factor: string;
  nMin: string;
  nMax: string;
  nHole: string;
  nOct: string;
  kOct: string;
  nHolesMin: string;
  hy: HyForm;
}

export type SearchFrom = "model" | "trend" | "manual";

export interface SearchForm {
  /** null — as the core decides: the model of variogram if there is one, else the trend, else by hand */
  from: SearchFrom | null;
  trendId: number | null;
  /** the angles and the radii typed; for «по модели» and «по тренду» they are what the core took (shown, not sent) */
  ang: Tri;
  r: Tri;
  /** null — the passes of the core (`defaults`) */
  passes: PassForm[] | null;
}

export interface OutputsForm {
  /** "" — the core names it «‹ПОЛЕ›_‹МЕТОД›_» */
  prefix: string;
  /** the keys of the fields; null — those of the method (`defaults`, + NHY with the rule of the gold-grade samples) */
  fields: string[] | null;
}

export const EMPTY_HY: HyForm = { on: false, t: "", q: "0,5", mode: "drop" };
export const EMPTY_SEARCH: SearchForm = { from: null, trendId: null, ang: ["", "", ""], r: ["", "", ""], passes: null };
export const EMPTY_OUTPUTS: OutputsForm = { prefix: "", fields: null };

/** A pass the core gives when none is typed: factor 1, 2, 3 …, n_min 4, n_max 24, no limits, no rule of the gold-grade samples. */
export const newPass = (i: number): PassForm => ({ factor: String(i + 1), nMin: "4", nMax: "24", nHole: "0", nOct: "0", kOct: "0", nHolesMin: "0", hy: { ...EMPTY_HY } });

const text = (v: unknown): string => (typeof v === "number" ? numberText(v) : typeof v === "string" ? v : "");

/** A pass of a stored row or of the defaults of the core (the form of `canonical`). */
export function passOfRaw(raw: unknown, i: number): PassForm {
  const o = obj(raw);
  const base = newPass(i);
  const h = obj(o.hy);
  return {
    factor: text(o.factor) || base.factor,
    nMin: text(o.n_min) || base.nMin,
    nMax: text(o.n_max) || base.nMax,
    nHole: text(o.n_hole) || base.nHole,
    nOct: text(o.n_oct) || base.nOct,
    kOct: text(o.k_oct) || base.kOct,
    nHolesMin: text(o.n_holes_min) || base.nHolesMin,
    hy: typeof h.t === "number" ? { on: true, t: numberText(h.t), q: text(h.q) || EMPTY_HY.q, mode: h.mode === "cap" ? "cap" : "drop" } : { ...EMPTY_HY },
  };
}

/** The passes of the core by default (`defaults.params.search.passes`); without an answer — the three of 8.8.2. */
export function defaultPasses(params: Record<string, unknown> | null | undefined): PassForm[] {
  const list = obj(obj(params).search).passes;
  return Array.isArray(list) && list.length ? list.map(passOfRaw) : [0, 1, 2].map(newPass);
}

/** The passes in the form: the ones typed, else those of the core. */
export const passesOf = (s: SearchForm, params: Record<string, unknown> | null | undefined): PassForm[] => s.passes ?? defaultPasses(params);

/** The search of a stored row (`meta.search`, `meta.trend_id`) → the form. */
export function searchOfMeta(raw: unknown, trendId: unknown): SearchForm {
  const s = obj(raw);
  const from = s.from === "model" || s.from === "trend" || s.from === "manual" ? s.from : null;
  const passes = Array.isArray(s.passes) && s.passes.length ? s.passes.map(passOfRaw) : null;
  return { from, trendId: typeof trendId === "number" ? trendId : null, ang: [text(s.ang1), text(s.ang2), text(s.ang3)], r: [text(s.r1), text(s.r2), text(s.r3)], passes };
}

export const outputsOfMeta = (raw: unknown): OutputsForm => {
  const o = obj(raw);
  return { prefix: typeof o.prefix === "string" ? o.prefix : "", fields: Array.isArray(o.fields) ? o.fields.filter((x): x is string => typeof x === "string") : null };
};

// ---- the search → the body ----

const PASS_FIELDS = [
  ["factor", "factor", "Множитель радиусов прохода"],
  ["n_min", "nMin", "Наименьшее число проб n_min"],
  ["n_max", "nMax", "Наибольшее число проб n_max"],
  ["n_hole", "nHole", "Проб из одной скважины n_скв"],
  ["n_oct", "nOct", "Проб в октанте n_окт"],
  ["k_oct", "kOct", "Заполненных октантов k_окт"],
  ["n_holes_min", "nHolesMin", "Наименьшее число скважин"],
] as const;

function passBody(p: PassForm, i: number): Record<string, unknown> | Issue {
  const at = `search.passes.${i}`;
  const out: Record<string, unknown> = {};
  for (const [key, prop, label] of PASS_FIELDS) {
    const v = numberIn(p[prop], RANGES[key], `Проход ${i + 1}: ${label}`, `${at}.${key}`);
    if (isIssue(v)) return v;
    out[key] = v;
  }
  if ((out.n_min as number) > (out.n_max as number)) return bad(`${at}.n_min`, `Проход ${i + 1}: n_min больше n_max.`, `Задайте n_min не больше ${out.n_max as number}.`);
  if (!p.hy.on) return { ...out, hy: null };
  const t = numberIn(p.hy.t, RANGES.hy_t, `Проход ${i + 1}: порог ураганных проб t`, `${at}.hy.t`);
  if (isIssue(t)) return t;
  const q = numberIn(p.hy.q, RANGES.hy_q, `Проход ${i + 1}: доля радиуса q`, `${at}.hy.q`);
  if (isIssue(q)) return q;
  return { ...out, hy: { t, q, mode: p.hy.mode } };
}

/** The effective source of the ellipsoid: the one chosen, else the one the core takes. */
export const effectiveFrom = (s: SearchForm, hasModel: boolean): SearchFrom => s.from ?? (hasModel ? "model" : s.trendId !== null ? "trend" : "manual");

export interface SearchBody {
  search?: Record<string, unknown>;
  trend_id?: number;
}

/** The `search` and the `trend_id` of the body; nothing typed — nothing sent (the core's defaults); a wrong value — the words under it. */
export function searchBody(s: SearchForm, hasModel: boolean): SearchBody | Issue {
  const from = effectiveFrom(s, hasModel);
  const search: Record<string, unknown> = {};
  if (s.from !== null) search.from = s.from;
  if (from === "model" && !hasModel) return bad("search.from", "Эллипсоид по модели вариограммы: модель не выбрана.", "Выберите модель вариограммы или задайте радиусы вручную.");
  if (from === "trend" && s.trendId === null) return bad("trend_id", "Эллипсоид по тренду: тренд не выбран.", "Выберите тренд или задайте углы вручную.");
  if (from === "manual") {
    if (!s.r[0].trim()) return bad("search.r1", "Радиус поиска r1 не задан.", "Задайте радиус от 0 до 100 000 м.");
    for (let i = 0; i < 3; i++) {
      const key = `ang${i + 1}` as "ang1" | "ang2" | "ang3";
      if (!s.ang[i].trim()) continue;
      const v = numberIn(s.ang[i], RANGES[key], `Угол ${i + 1} эллипсоида поиска`, `search.${key}`);
      if (isIssue(v)) return v;
      search[key] = v;
    }
  }
  // the radii: by hand — r1 is needed, r2 and r3 are r1 when empty; a trend gives the angles and the ratios, r1 is the size of it unless typed
  const radii = from === "manual" ? [0, 1, 2] : from === "trend" && s.r[0].trim() ? [0] : [];
  for (const i of radii) {
    if (i > 0 && !s.r[i].trim()) continue;
    const key = `r${i + 1}` as "r1" | "r2" | "r3";
    const v = numberIn(s.r[i], RANGES[key], `Радиус поиска ${key}`, `search.${key}`);
    if (isIssue(v)) return v;
    search[key] = v;
  }
  if (s.passes) {
    const list: Record<string, unknown>[] = [];
    if (!s.passes.length) return bad("search.passes", "Не задано ни одного прохода.", `Задайте от 1 до ${PASSES_MAX} проходов.`);
    if (s.passes.length > PASSES_MAX) return bad("search.passes", `Проходов больше ${PASSES_MAX}.`, `Оставьте не больше ${PASSES_MAX}.`);
    for (let i = 0; i < s.passes.length; i++) {
      const p = passBody(s.passes[i], i);
      if (isIssue(p)) return p;
      list.push(p);
    }
    search.passes = list;
  }
  return { ...(Object.keys(search).length ? { search } : {}), ...(s.trendId !== null ? { trend_id: s.trendId } : {}) };
}

/** Is the rule of the gold-grade samples on in any pass of the form (the field NHY joins the default fields)? */
export const anyHy = (passes: readonly PassForm[]): boolean => passes.some((p) => p.hy.on);

// ---- the output fields ----

export const OUTPUT_KEYS = ["STATUS", "EST", "KV", "NS", "NH", "NHY", "AD", "DMIN", "PASS", "SLOPE", "KE", "LAGR", "SNW", "NOCT", "P", "ETYPE"] as const;
const NEAR = ["NS", "NH", "NHY", "AD", "DMIN", "PASS"];

/** The keys a method may write (`spec_parts.ALLOWED`; a test compares them with `params.outputs.allowed` of the answer of the core). */
export const ALLOWED_FIELDS: Record<MethodKind, readonly string[]> = {
  nn: ["STATUS", "EST", ...NEAR, "NOCT"],
  idw: ["STATUS", "EST", ...NEAR, "NOCT"],
  sk: ["STATUS", "EST", "KV", ...NEAR, "SLOPE", "KE", "SNW", "NOCT"],
  ok: OUTPUT_KEYS.slice(0, 14),
  ik: ["STATUS", ...NEAR, "NOCT", "P", "ETYPE"],
};

/** The keys a method writes by default (`spec_parts.DEFAULT_FIELDS`). */
export const DEFAULT_FIELDS: Record<MethodKind, readonly string[]> = {
  nn: ["STATUS", "EST", "DMIN", "PASS"],
  idw: ["STATUS", "EST", "NS", "AD", "PASS"],
  sk: ["STATUS", "EST", "KV", "NS", "NH", "AD", "PASS", "SLOPE"],
  ok: ["STATUS", "EST", "KV", "NS", "NH", "AD", "PASS", "SLOPE", "KE"],
  ik: ["STATUS", "NS", "PASS", "P", "ETYPE"],
};

/** The words of a field of 8.8.2 (the name of the field in the model is `‹префикс›‹КЛЮЧ›`). */
export const FIELD_TITLES: Record<string, string> = {
  STATUS: "статус блока: 0 — оценён, 1 — вне домена, 2 — мало проб, 3 — вырожденная система (пишется всегда)",
  EST: "оценка содержания",
  KV: "дисперсия кригинга",
  NS: "число проб в оценке",
  NH: "число скважин",
  NHY: "число затронутых ураганных проб (правило Д-29)",
  AD: "среднее расстояние до проб, м",
  DMIN: "наименьшее расстояние до пробы, м",
  PASS: "номер прохода, давшего оценку",
  SLOPE: "наклон регрессии",
  KE: "эффективность кригинга",
  LAGR: "множитель Лагранжа μ",
  SNW: "сумма отрицательных весов",
  NOCT: "число заполненных октантов",
  P: "вероятности P1…PK по порогам",
  ETYPE: "E-type — оценка содержания по вероятностям",
};

/** «AU_OK_»: the field in upper case, every character that is not a letter, a digit or «_» — «_» (`spec_parts.default_prefix`). */
export function defaultPrefix(field: string, kind: MethodKind): string {
  const base = Array.from(field.toUpperCase().replace(/[^\p{L}\p{N}_]/gu, "_")).slice(0, PREFIX_MAX - kind.length - 2).join("");
  return `${base}_${kind.toUpperCase()}_`;
}

/** The keys written: the typed ones (the keys of the method only, STATUS first), else those of the method (+ NHY with the rule of Д-29). */
export function effectiveFields(kind: MethodKind, o: OutputsForm, hy: boolean, defaults: Record<string, readonly string[]> = DEFAULT_FIELDS, allowed: readonly string[] = ALLOWED_FIELDS[kind]): string[] {
  const keys = new Set<string>(o.fields ? ["STATUS", ...o.fields] : [...(defaults[kind] ?? DEFAULT_FIELDS[kind]), ...(hy ? ["NHY"] : [])]);
  return allowed.filter((k) => keys.has(k));
}

/** The names of the fields of the model: `P` of IK is P1…PK. */
export function fieldNames(prefix: string, keys: readonly string[], thresholds: number): string[] {
  return keys.flatMap((k) => (k === "P" ? Array.from({ length: thresholds }, (_, i) => `${prefix}P${i + 1}`) : [`${prefix}${k}`]));
}

/** The names of the fields that the model has already. */
export const existingFields = (modelFields: readonly string[], names: readonly string[]): string[] => {
  const have = new Set(modelFields);
  return names.filter((n) => have.has(n));
};

const PREFIX_RE = /^[\p{L}\p{N}_]{1,40}$/u;

/** The `outputs` of the body: the prefix and the keys typed; nothing typed — nothing sent. */
export function outputsBody(o: OutputsForm, kind: MethodKind): { outputs?: Record<string, unknown> } | Issue {
  const prefix = o.prefix.trim();
  if (prefix && !PREFIX_RE.test(prefix)) return bad("outputs.prefix", prefix.length > PREFIX_MAX ? `Префикс полей длиннее ${PREFIX_MAX} знаков.` : "Префикс полей: допустимы буквы, цифры и «_».", "Например, AU_OK_.");
  const out: Record<string, unknown> = {};
  if (prefix) out.prefix = prefix;
  if (o.fields) out.fields = ALLOWED_FIELDS[kind].filter((k) => k === "STATUS" || o.fields!.includes(k));
  return Object.keys(out).length ? { outputs: out } : {};
}
