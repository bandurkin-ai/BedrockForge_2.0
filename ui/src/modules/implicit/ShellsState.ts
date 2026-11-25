/**
 * The group «Оболочка содержаний» (ТЗ 8.9.2; ADR 0026 решения 6, 16; docs/progress/e19-api.md §4): the values g of a field of the composites of
 * a result (`source: composites`, `result_id`) or of a table of samples (`source: table`, `set_id?`, `table`), the transform T of the values
 * (linear, logarithm, normal scores), the thresholds (1–20, strictly increasing) and the mode — numeric (one RBF of T(g), the surface f = T(t) −
 * RBF) or indicator (RBF of [g ≥ t] − 0,5 on every threshold, the surface p − 0,5, p 0,3–0,7). The shell of the threshold k is cut by the shell
 * of the threshold k − 1 (inside f < 0): the shells are nested. The typed fields stay text («0,5»). No React.
 */
import { numberText, parseNumber } from "../composites/params";
import { bad, isIssue, numberIn, type Issue } from "../estimation/formBase";
import type { GroupMeta } from "./api";
import { boundsOf, THRESHOLDS_MAX, type KindSpec, type Made } from "./groupBase";

export type ShellSource = "composites" | "table";
export type Transform = "linear" | "log" | "nscore";
export type ShellMode = "numeric" | "indicator";

export const SOURCES: readonly ShellSource[] = ["composites", "table"];
export const TRANSFORMS: readonly Transform[] = ["linear", "log", "nscore"];
export const MODES: readonly ShellMode[] = ["numeric", "indicator"];

export const SOURCE_TITLES: Record<ShellSource, string> = { composites: "Композиты (результат обработки проб)", table: "Таблица проб набора скважин" };
export const TRANSFORM_TITLES: Record<Transform, string> = { linear: "Линейно (без преобразования)", log: "Логарифм", nscore: "Нормальные оценки (nscore)" };
export const MODE_TITLES: Record<ShellMode, string> = { numeric: "Числовая: изоповерхность f = t", indicator: "Индикаторная: значения [g ≥ t] − 0,5, изоповерхность p − 0,5" };

export interface ShellForm {
  name: string;
  source: ShellSource;
  /** the set of the table (of the result — only to list its results); null — the set of the model's data */
  setId: number | null;
  resultId: number | null;
  /** the key of the table (the id as text) */
  table: string | null;
  field: string | null;
  transform: Transform;
  mode: ShellMode;
  /** «0,5; 1; 2,5» */
  thresholds: string;
  p: string;
}

export const EMPTY_SHELL: ShellForm = { name: "", source: "composites", setId: null, resultId: null, table: null, field: null, transform: "linear", mode: "numeric", thresholds: "", p: "" };

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const text = (v: number): string => numberText(v);

/** «0,5; 1; 2,5» → the numbers, or the place of the first wrong word (the index of the threshold). */
export function parseThresholds(raw: string): { ok: true; values: number[] } | { ok: false; index: number; word: string } {
  const words = raw.split(/[;\s]+/).filter(Boolean);
  const values: number[] = [];
  for (let i = 0; i < words.length; i++) {
    const v = parseNumber(words[i].replace(/−/g, "-"));
    if (v === null) return { ok: false, index: i, word: words[i] };
    values.push(v);
  }
  return { ok: true, values };
}

/** The thresholds as text for a command or a field: «0,5; 1; 2,5» (the whole precision: a command must give the same numbers back). */
export const thresholdsText = (values: readonly number[]): string => values.map((v) => String(v).replace(".", ",")).join("; ");

/** The title of the shell of a threshold: «≥ 0,5». */
export const shellTitle = (t: number, transform: Transform): string => `${transform === "log" ? "ln g" : "g"} ≥ ${text(t)}`;

export function shellFromGroup(g: GroupMeta): ShellForm {
  const p = g.params;
  const th = Array.isArray(p.thresholds) ? p.thresholds.filter(num) : [];
  return {
    name: g.name,
    source: p.source === "table" ? "table" : "composites",
    setId: num(p.set_id) ? p.set_id : null,
    resultId: num(p.result_id) ? p.result_id : null,
    table: p.table !== null && p.table !== undefined ? String(p.table) : null,
    field: typeof p.field === "string" ? p.field : null,
    transform: p.transform === "log" || p.transform === "nscore" ? p.transform : "linear",
    mode: p.mode === "indicator" ? "indicator" : "numeric",
    thresholds: th.map(text).join("; "),
    p: p.mode === "indicator" && num(p.p) && p.p !== 0.5 ? text(p.p) : "",
  };
}

const oneOf = <T extends string>(v: unknown, list: readonly T[], param: string, label: string): T | Issue => (typeof v === "string" && (list as readonly string[]).includes(v) ? (v as T) : bad(param, `${label}: «${String(v)}» не подходит.`, `Допустимо: ${list.join(", ")}.`));

/** The arguments of `implicit.shells` over a base form. */
export function shellFromArgs(a: Record<string, unknown>, base: ShellForm): ShellForm | Issue {
  const f: ShellForm = { ...base };
  if (typeof a.name === "string") f.name = a.name;
  if (a.source !== undefined) {
    const v = oneOf(a.source, SOURCES, "params.source", "Источник значений");
    if (isIssue(v)) return v;
    f.source = v;
  }
  if (num(a.set_id)) f.setId = a.set_id;
  if (num(a.result_id)) {
    f.resultId = a.result_id;
    if (a.source === undefined) f.source = "composites";
  }
  if (a.table !== undefined && a.table !== null && String(a.table).trim()) {
    f.table = String(a.table).trim();
    if (a.source === undefined) f.source = "table";
  }
  if (typeof a.field === "string") f.field = a.field.trim() || null;
  if (a.transform !== undefined) {
    const v = oneOf(a.transform, TRANSFORMS, "params.transform", "Преобразование");
    if (isIssue(v)) return v;
    f.transform = v;
  }
  if (a.mode !== undefined) {
    const v = oneOf(a.mode, MODES, "params.mode", "Вид оболочки");
    if (isIssue(v)) return v;
    f.mode = v;
  }
  if (typeof a.thresholds === "string") f.thresholds = a.thresholds;
  if (a.p !== undefined) f.p = typeof a.p === "number" ? text(a.p) : String(a.p);
  return f;
}

/** The body of the group (`{name, type, codes, params}`) or the first wrong thing under its field. */
export function shellMake(f: ShellForm, ctx: Parameters<KindSpec<ShellForm>["make"]>[1]): Made {
  const fail = (issue: Issue): Made => ({ ok: false, issue });
  const name = f.name.trim();
  if (!name) return fail(bad("name", "У оболочки нет названия.", "Назовите группу, например «Au 0,5 г/т»."));
  const params: Record<string, unknown> = { source: f.source };
  if (f.source === "composites") {
    if (f.resultId === null) return fail(bad("params.result_id", "Не выбран результат обработки проб.", "Выберите результат с композитами (инструмент «Композиты») или переключитесь на таблицу проб."));
    params.result_id = f.resultId;
  } else {
    if (!f.table) return fail(bad("params.table", "Не выбрана таблица проб.", "Выберите набор скважин и таблицу с содержаниями."));
    if (f.setId !== null) params.set_id = f.setId;
    else if (!ctx.model.setId) return fail(bad("params.set_id", "Не выбран набор скважин.", "Выберите набор скважин таблицы проб."));
    params.table = f.table;
  }
  if (!f.field) return fail(bad("params.field", "Не выбрано поле содержания.", "Выберите числовое поле таблицы."));
  params.field = f.field;
  params.transform = f.transform;
  params.mode = f.mode;

  const th = parseThresholds(f.thresholds);
  if (!th.ok) return fail(bad(`params.thresholds.${th.index}`, `Порог ${th.index + 1}: «${th.word}» — не число.`, "Введите пороги числами через «;», например 0,5; 1; 2,5."));
  const values = th.values;
  if (!values.length) return fail(bad("params.thresholds", "Не заданы пороги.", "Введите хотя бы один порог содержания, например 0,5."));
  if (values.length > THRESHOLDS_MAX) return fail(bad(`params.thresholds.${THRESHOLDS_MAX}`, `Порогов больше ${THRESHOLDS_MAX}.`, "Оставьте не больше двадцати порогов."));
  for (let i = 0; i < values.length; i++) {
    if (f.transform === "log" && values[i] <= 0) return fail(bad(`params.thresholds.${i}`, `Порог ${i + 1}: при логарифме порог должен быть больше нуля.`, "Задайте положительный порог или выберите другое преобразование."));
    if (i > 0 && values[i] <= values[i - 1]) return fail(bad(`params.thresholds.${i}`, `Порог ${i + 1} не больше предыдущего.`, "Пороги идут строго по возрастанию: оболочка порога k вложена в оболочку k − 1."));
  }
  params.thresholds = values;

  if (f.mode === "indicator" && f.p.trim() !== "") {
    const p = numberIn(f.p, boundsOf(ctx.ranges, "p"), "Вероятность p", "params.p");
    if (isIssue(p)) return fail(p);
    params.p = p;
  }
  return { ok: true, group: { name, type: "shell", codes: [], params } };
}

/** The arguments of the command for a made group. */
export function shellArgs(group: Record<string, unknown>): Record<string, unknown> {
  const p = (group.params ?? {}) as Record<string, unknown>;
  return {
    name: group.name,
    source: p.source,
    ...(num(p.result_id) ? { result_id: p.result_id } : {}),
    ...(num(p.set_id) ? { set_id: p.set_id } : {}),
    ...(p.table !== undefined ? { table: String(p.table) } : {}),
    field: p.field,
    transform: p.transform,
    mode: p.mode,
    thresholds: thresholdsText(p.thresholds as number[]),
    ...(num(p.p) ? { p: p.p } : {}),
  };
}

export const shellSpec: KindSpec<ShellForm> = {
  kind: "shell",
  window: "implicit-shells",
  command: "implicit.shells",
  title: "Оболочки содержаний",
  type: "shell",
  nameBase: "Оболочка",
  empty: (model) => ({ ...EMPTY_SHELL, setId: model.setId }),
  fromGroup: (g) => shellFromGroup(g),
  fromArgs: (a, base) => shellFromArgs(a, base),
  make: shellMake,
  complete: (a) => typeof a.thresholds === "string" && a.thresholds.trim() !== "",
  argsOf: shellArgs,
  nameOf: (f) => f.name,
  withName: (f, name) => ({ ...f, name }),
};
