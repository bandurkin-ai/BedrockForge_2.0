/**
 * The structural trend (ТЗ 7.35 Д-15, ТЗ 8.9.2; ADR 0026 решение 27, вопрос № 97): the axes of the anisotropy follow the
 * attitude of the structure from point to point. The window «Тренд» of that kind takes the source — the measurements
 * «точка, α, Aп» (a set, a table, the fields α and Aп, the depth or, without it, the middle of FROM–TO) or a supporting
 * wireframe (with the radius of averaging of its normal r_n) — R_тр (the radius of influence of the measurements,
 * 10–5000 m, 200), the ratios a1 : a2 : a3, the size and the colour. The values as typed (the form), their check before
 * a request (out of range never reaches the core), the body of `POST` / `PUT` and the arguments of `trend.save`
 * (`structuralArgs`, `structuralBody`). No React.
 */
import type { Trend, TrendBody, TrendSource } from "./api";
import { bodyOfArgs, checkValues, EMPTY_STRUCTURAL, numText, parseNum, RADIUS_RANGE, valuesOf, type StructuralValues, type TrendEdit, type TrendShape } from "./state";

export { EMPTY_STRUCTURAL, RADIUS_RANGE, type SourceType, type StructuralValues } from "./state";
export const R_N_RANGE = { lo: 0, hi: 200 } as const;
export const DISCS_NOTE = "Диск — плоскость замера: круг с нормалью замера (верхней), в точке замера; у каркаса — в центрах граней";

/** The form of a stored structural trend. */
export function structuralOf(t: Trend): StructuralValues {
  const s = t.source;
  const base = { ...EMPTY_STRUCTURAL, radius: numText(t.radius ?? RADIUS_RANGE.dflt) };
  if (!s) return base;
  if (s.type === "mesh") return { ...base, type: "mesh", wireframeId: s.wireframe_id, rN: numText(s.r_n ?? 0) };
  return { ...base, type: "measurements", setId: s.set_id, table: s.table, alpha: s.alpha, dipdir: s.dipdir, depth: s.depth ?? "" };
}

export type StructuralParam = "set" | "table" | "alpha" | "dipdir" | "depth" | "wireframe" | "r_n" | "radius";
export type CheckedSource = { ok: true; source: TrendSource; radius: number } | { ok: false; param: StructuralParam; error: string };

const bad = (param: StructuralParam, error: string): CheckedSource => ({ ok: false, param, error });

/** The source and R_тр checked (the ranges of the core: `trend_struct.radius_of`, `r_n_of`). */
export function checkSource(v: StructuralValues): CheckedSource {
  const radius = parseNum(v.radius);
  if (radius === null || radius < RADIUS_RANGE.lo || radius > RADIUS_RANGE.hi) return bad("radius", "Радиус влияния замеров R_тр: число от 10 до 5000 м");
  if (v.type === "mesh") {
    if (v.wireframeId === null) return bad("wireframe", "Выберите опорный каркас или поверхность");
    const rN = parseNum(v.rN);
    if (rN === null || rN < R_N_RANGE.lo || rN > R_N_RANGE.hi) return bad("r_n", "Радиус осреднения нормали r_n: число от 0 до 200 м (0 — нормаль ближайшего треугольника)");
    return { ok: true, radius, source: rN > 0 ? { type: "mesh", wireframe_id: v.wireframeId, r_n: rN } : { type: "mesh", wireframe_id: v.wireframeId } };
  }
  if (v.setId === null) return bad("set", "Выберите набор скважин с замерами");
  if (!v.table) return bad("table", "Выберите таблицу замеров");
  if (!v.alpha) return bad("alpha", "Выберите поле угла падения α");
  if (!v.dipdir) return bad("dipdir", "Выберите поле азимута падения Aп");
  if (v.alpha === v.dipdir) return bad("dipdir", "Угол падения α и азимут падения Aп — разные поля");
  const source: TrendSource = { type: "measurements", set_id: v.setId, table: v.table, alpha: v.alpha, dipdir: v.dipdir };
  if (v.depth) source.depth = v.depth;
  return { ok: true, radius, source };
}

export type CheckedStructural = { ok: true; body: TrendBody; shape: TrendShape } | { ok: false; param: string; error: string };

/** The values of the form of a structural trend: the shape (ratios, size, colour) is checked as a global one's, the angles
 * and the centre are not its own. */
export function checkStructural(e: Pick<TrendEdit, "values" | "structural">): CheckedStructural {
  const c = checkValues({ ...e.values, ang1: "0", ang2: "0", ang3: "0", cx: "", cy: "", cz: "" });
  if (!c.ok) return c;
  const s = checkSource(e.structural);
  if (!s.ok) return s;
  const body: TrendBody = { kind: "structural", source: s.source, radius: s.radius, ratios: c.shape.ratios, size: c.shape.size, color: c.shape.color };
  if (c.shape.name) body.name = c.shape.name;
  return { ok: true, body, shape: c.shape };
}

/** The arguments of `trend.save` of a structural trend of the window (`source`, `set_id`, `table`, `alpha`, `dipdir`,
 * `depth`, `wireframe_id`, `r_n`, `radius`, `ratios`, `size`, `color`); a reason when the values are wrong. */
export function structuralArgs(e: TrendEdit): { args: Record<string, unknown> } | { param: string; error: string } {
  const c = checkStructural(e);
  if (!c.ok) return { param: c.param, error: c.error };
  const s = c.body.source!;
  const args: Record<string, unknown> = { kind: "structural", source: s.type, radius: c.body.radius, ratios: `${numText(c.shape.ratios[1])}; ${numText(c.shape.ratios[2])}`, size: c.shape.size, color: c.shape.color };
  if (c.shape.name) args.name = c.shape.name;
  if (e.trendId !== null) args.trend_id = e.trendId;
  if (e.trendId !== null && typeof e.version === "number") args.expected_version = e.version;
  if (s.type === "mesh") {
    args.wireframe_id = s.wireframe_id;
    if (s.r_n !== undefined) args.r_n = s.r_n;
  } else {
    Object.assign(args, { set_id: s.set_id, table: s.table, alpha: s.alpha, dipdir: s.dipdir });
    if (s.depth) args.depth = s.depth;
  }
  return { args };
}

const given = (a: Record<string, unknown>, k: string) => a[k] !== undefined && a[k] !== null && (typeof a[k] !== "string" || (a[k] as string).trim() !== "");
const word = (a: Record<string, unknown>, k: string): string => (typeof a[k] === "number" ? String(a[k]) : typeof a[k] === "string" ? (a[k] as string).trim() : "");

/** The source and R_тр of the arguments of `trend.save` (the names of the table and the fields as given — the core
 * finds them); undefined — none of them is given (an update of the other fields); a string — what is wrong. */
export function sourceOfArgs(a: Record<string, unknown>): { source?: TrendSource; radius?: number } | { param: string; error: string } {
  const out: { source?: TrendSource; radius?: number } = {};
  if (given(a, "radius")) {
    const r = typeof a.radius === "number" ? a.radius : parseNum(String(a.radius));
    if (r === null || r < RADIUS_RANGE.lo || r > RADIUS_RANGE.hi) return { param: "radius", error: "Радиус влияния замеров R_тр: число от 10 до 5000 м" };
    out.radius = r;
  }
  const wire = given(a, "wireframe_id");
  const kind = word(a, "source") || (wire ? "mesh" : given(a, "set_id") || given(a, "table") ? "measurements" : "");
  if (!kind) return out;
  if (kind === "mesh") {
    const id = Number(a.wireframe_id);
    if (!wire || !Number.isInteger(id) || id <= 0) return { param: "wireframe_id", error: "wireframe_id — номер опорного каркаса или поверхности" };
    const source: TrendSource = { type: "mesh", wireframe_id: id };
    if (given(a, "r_n")) {
      const rN = typeof a.r_n === "number" ? a.r_n : parseNum(String(a.r_n));
      if (rN === null || rN < R_N_RANGE.lo || rN > R_N_RANGE.hi) return { param: "r_n", error: "Радиус осреднения нормали r_n: число от 0 до 200 м" };
      if (rN > 0) source.r_n = rN;
    }
    return { ...out, source };
  }
  if (kind !== "measurements") return { param: "source", error: "Источник структурного тренда: measurements — замеры, mesh — каркас" };
  const set = Number(a.set_id);
  if (!given(a, "set_id") || !Number.isInteger(set) || set <= 0) return { param: "set_id", error: "set_id — номер набора скважин с замерами" };
  for (const [k, text] of [["table", "table — таблица замеров набора: имя или номер"], ["alpha", "alpha — поле угла падения α"], ["dipdir", "dipdir — поле азимута падения Aп"]] as const) if (!word(a, k)) return { param: k, error: text };
  const source: TrendSource = { type: "measurements", set_id: set, table: word(a, "table"), alpha: word(a, "alpha"), dipdir: word(a, "dipdir") };
  if (word(a, "depth")) source.depth = word(a, "depth");
  return { ...out, source };
}

/** «Замеры: набор «Скважины», таблица «Замеры», α «ALPHA», Aп «DIPDIR»» / «Каркас «Кровля»»; a source that is gone — said so. */
export function sourceText(t: Pick<Trend, "source" | "sourceInfo" | "sourceLost">): string {
  const s = t.source;
  const info = t.sourceInfo;
  if (!s) return "Источник не задан";
  const gone = t.sourceLost ? " — удалён или недоступен" : "";
  if (s.type === "mesh") return `Опорный каркас ${info?.wireframe_name ? `«${info.wireframe_name}»` : `№${s.wireframe_id}`}${s.r_n ? `, r_n ${numText(s.r_n)} м` : ""}${gone}`;
  const where = `набор ${info?.set_name ? `«${info.set_name}»` : `№${s.set_id}`}, таблица ${info?.table_name ? `«${info.table_name}»` : `№${s.table}`}`;
  return `Замеры: ${where}, α «${s.alpha}», Aп «${s.dipdir}», ${s.depth ? `глубина «${s.depth}»` : "середина интервала"}${gone}`;
}

/** The tooltip of a row of the tree and the line of the window: «Структурный тренд «Кровля»: R_тр 200 м; 1 : 0,36 : 0,05». */
export function structuralSummary(t: Trend): string {
  const r = (v: number) => v.toLocaleString("ru-RU", { maximumSignificantDigits: 4 });
  const n = t.measurements !== null ? `; замеров ${t.measurements.toLocaleString("ru-RU")}` : "";
  return `Структурный тренд «${t.name}»: R_тр ${t.radius === null ? "—" : `${r(t.radius)} м`}; 1 : ${r(t.ratios[1])} : ${r(t.ratios[2])}${n}${t.used ? "; используется" : ""}`;
}

/** A structural trend has no angles, centre or model: its axes come from the source. */
export function notStructural(a: Record<string, unknown>): { param: string; error: string } | null {
  for (const k of ["ang1", "ang2", "ang3", "centre", "model_id", "structure"]) if (given(a, k)) return { param: k, error: `У структурного тренда нет «${k}»: оси берутся из источника — замеров или каркаса` };
  return null;
}

/** The body of `POST` / `PUT` of the arguments of `trend.save` with `kind: "structural"` (the shape — ratios, size, colour,
 * name — as of a global trend; no angles, no centre, no model). Checked: out of range never reaches the core. */
export function structuralBodyOfArgs(a: Record<string, unknown>): { body: TrendBody } | { param: string; error: string } {
  const own = notStructural(a);
  if (own) return own;
  const shape = bodyOfArgs({ name: a.name, ratios: a.ratios, size: a.size, color: a.color });
  if ("error" in shape) return shape;
  const src = sourceOfArgs(a);
  if ("error" in src) return src;
  return { body: { ...shape.body, kind: "structural", ...src } };
}

/** What a window «Тренд» takes of a stored trend (a new window, a trend read again, a trend saved): the values, the origin,
 * the version, the kind and, of a structural one, its source. */
export function editFieldsOf(t: Trend): Pick<TrendEdit, "trendId" | "kind" | "structural" | "values" | "origin" | "version"> {
  return { trendId: t.id, kind: t.kind, structural: t.kind === "structural" ? structuralOf(t) : { ...EMPTY_STRUCTURAL }, values: valuesOf(t), origin: t.origin, version: t.version };
}
