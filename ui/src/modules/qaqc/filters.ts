/**
 * The filter of the QAQC records (ADR 0020 §5; ТЗ 8.4.2): JSON `v: 1` — labs, batches, dates, CRM, types,
 * the prefix of the hole and up to 20 conditions «любое поле» joined by «И». The element is not a filter of rows
 * (the tab picks its columns). One filter for every subtab, chart and the report; the core makes it a row mask.
 * Here: the edit, the reset, the stored form, the body sent to the core (only the conditions set), the arguments
 * of `qaqc.filter`, the label «записей N из M». Pure (tested in node).
 */
import { QAQC_TYPES, type QaqcType } from "./commands";

export type FieldKind = "text" | "number" | "date";
export type TextOp = "eq" | "ne" | "in" | "prefix" | "contains" | "empty";
export type RangeOp = "eq" | "ge" | "le" | "between" | "empty";
export type WhereOp = TextOp | RangeOp;
export type WhereValue = string | number | null | (string | number)[];

export interface WhereCond {
  field: string;
  op: WhereOp;
  /** `in` — a list, `between` — [from, to], `empty` — none */
  value?: WhereValue;
}

export interface QaqcFilter {
  v: 1;
  labs: string[];
  batches: string[];
  /** ISO dates «ГГГГ-ММ-ДД», null — open */
  date_from: string | null;
  date_to: string | null;
  crm: string[];
  types: QaqcType[];
  hole_prefix: string | null;
  where: WhereCond[];
}

export const MAX_WHERE = 20;
/** Values of one list condition kept (a list of batches can be long, but not endless). */
export const MAX_VALUES = 5000;

export const EMPTY_FILTER: QaqcFilter = { v: 1, labs: [], batches: [], date_from: null, date_to: null, crm: [], types: [], hole_prefix: null, where: [] };

export const TEXT_OPS: readonly TextOp[] = ["eq", "ne", "in", "prefix", "contains", "empty"];
export const RANGE_OPS: readonly RangeOp[] = ["eq", "ge", "le", "between", "empty"];
export const OP_TITLES: Record<WhereOp, string> = { eq: "равно", ne: "не равно", in: "одно из", prefix: "начинается с", contains: "содержит", empty: "пусто", ge: "не меньше", le: "не больше", between: "между" };

export const opsFor = (kind: FieldKind): readonly WhereOp[] => (kind === "text" ? TEXT_OPS : RANGE_OPS);

const isText = (v: unknown): v is string => typeof v === "string";
const clean = (v: string) => v.trim();

/** Distinct non-empty texts in their order, at most `MAX_VALUES`. */
function texts(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out = new Set<string>();
  for (const v of raw) {
    const t = typeof v === "number" && Number.isFinite(v) ? String(v) : isText(v) ? clean(v) : "";
    if (t) out.add(t);
    if (out.size >= MAX_VALUES) break;
  }
  return [...out];
}

/** «ГГГГ-ММ-ДД», «ДД.ММ.ГГГГ», «ДД/ММ/ГГГГ» (a time after it is dropped) → «ГГГГ-ММ-ДД»; a wrong date — null. */
export function parseDate(text: unknown): string | null {
  if (!isText(text)) return null;
  const t = text.trim();
  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?$/.exec(t);
  let y: number, mo: number, d: number;
  if (m) [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  else {
    m = /^(\d{1,2})[./](\d{1,2})[./](\d{4})(?:[ T].*)?$/.exec(t);
    if (!m) return null;
    [d, mo, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
  }
  const at = new Date(Date.UTC(y, mo - 1, d));
  if (at.getUTCFullYear() !== y || at.getUTCMonth() !== mo - 1 || at.getUTCDate() !== d) return null;
  return `${String(y).padStart(4, "0")}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** «ГГГГ-ММ-ДД» → «ДД.ММ.ГГГГ» for people. */
export const dateTitle = (iso: string): string => iso.replace(/^(\d{4})-(\d{2})-(\d{2})$/, "$3.$2.$1");

const isOp = (op: unknown, kinds: readonly WhereOp[]): op is WhereOp => isText(op) && (kinds as readonly string[]).includes(op);
const ALL_OPS: readonly WhereOp[] = [...new Set<WhereOp>([...TEXT_OPS, ...RANGE_OPS])];
const scalar = (v: unknown): v is string | number => isText(v) || (typeof v === "number" && Number.isFinite(v));

/** A condition «любое поле» as stored, or a Russian reason why it is wrong. */
export function checkWhere(raw: unknown): WhereCond | string {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return "Условие должно быть объектом {field, op, value}";
  const r = raw as Record<string, unknown>;
  const field = isText(r.field) ? r.field.trim() : "";
  if (!field) return "В условии не задано поле";
  if (!isOp(r.op, ALL_OPS)) return `Условие «${field}»: операция — одна из ${ALL_OPS.join(", ")}`;
  const op = r.op;
  if (op === "empty") return { field, op };
  if (op === "in") {
    const list = Array.isArray(r.value) ? r.value.filter(scalar).slice(0, MAX_VALUES) : scalar(r.value) ? [r.value] : [];
    return list.length ? { field, op, value: list } : `Условие «${field}»: «одно из» — список значений`;
  }
  if (op === "between") {
    const pair = Array.isArray(r.value) ? r.value : [];
    if (pair.length !== 2 || !scalar(pair[0]) || !scalar(pair[1])) return `Условие «${field}»: «между» — два значения [от, до]`;
    return { field, op, value: [pair[0], pair[1]] };
  }
  if (!scalar(r.value) || (isText(r.value) && !r.value.trim())) return `Условие «${field}»: не задано значение`;
  return { field, op, value: isText(r.value) ? r.value.trim() : r.value };
}

/** A stored or received filter: what is valid is kept, the rest is dropped (an older or damaged form). */
export function normalizeFilter(raw: unknown): QaqcFilter {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return EMPTY_FILTER;
  const r = raw as Record<string, unknown>;
  const where = Array.isArray(r.where) ? r.where.map(checkWhere).filter((c): c is WhereCond => typeof c !== "string").slice(0, MAX_WHERE) : [];
  const prefix = isText(r.hole_prefix) ? r.hole_prefix.trim() : "";
  return {
    v: 1,
    labs: texts(r.labs),
    batches: texts(r.batches),
    date_from: parseDate(r.date_from),
    date_to: parseDate(r.date_to),
    crm: texts(r.crm),
    types: texts(r.types).map((t) => t.toUpperCase()).filter((t): t is QaqcType => (QAQC_TYPES as readonly string[]).includes(t)),
    hole_prefix: prefix || null,
    where,
  };
}

export function isEmptyFilter(f: QaqcFilter): boolean {
  return !f.labs.length && !f.batches.length && !f.date_from && !f.date_to && !f.crm.length && !f.types.length && !f.hole_prefix && !f.where.length;
}

/** Number of conditions set (a list counts as one). */
export function conditionCount(f: QaqcFilter): number {
  return [f.labs.length, f.batches.length, f.date_from || f.date_to, f.crm.length, f.types.length, f.hole_prefix].filter(Boolean).length + f.where.length;
}

export type FilterPatch = Partial<Omit<QaqcFilter, "v">>;

/** The filter with the given conditions replaced (normalized as stored). */
export const patchFilter = (f: QaqcFilter, patch: FilterPatch): QaqcFilter => normalizeFilter({ ...f, ...patch });

/** The list with the value added or removed. */
export function toggleValue<T extends string>(list: readonly T[], value: T, on?: boolean): T[] {
  const has = list.includes(value);
  const want = on ?? !has;
  return want === has ? [...list] : want ? [...list, value] : list.filter((v) => v !== value);
}

/** The filter of the body of `POST /qaqc/calc/*` and of the report: `v: 1` and only the conditions set. */
export function filterJson(f: QaqcFilter): Record<string, unknown> {
  const out: Record<string, unknown> = { v: 1 };
  for (const key of ["labs", "batches", "crm", "types", "where"] as const) if (f[key].length) out[key] = f[key];
  if (f.date_from) out.date_from = f.date_from;
  if (f.date_to) out.date_to = f.date_to;
  if (f.hole_prefix) out.hole_prefix = f.hole_prefix;
  return out;
}

/** The key of the filter for comparisons and caches (the same conditions — the same text). */
export const filterKey = (f: QaqcFilter): string => JSON.stringify(filterJson(f));

/** A list argument of a command: a JSON array or comma-separated text; empty text — an empty list. */
export function listArg(v: unknown): string[] | string {
  if (Array.isArray(v)) return texts(v);
  if (!isText(v)) return "Список — текст через запятую или JSON-массив";
  const t = v.trim();
  if (t.startsWith("[")) {
    try {
      const parsed: unknown = JSON.parse(t);
      if (Array.isArray(parsed)) return texts(parsed);
    } catch {
      /* fall through: the reason below */
    }
    return "Список в виде JSON не разобран: ожидается массив, например [\"ALS\", \"SGS\"]";
  }
  return texts(t.split(","));
}

/** The filter after `qaqc.filter` with these arguments, or a Russian reason of a refusal. */
export function filterFromArgs(current: QaqcFilter, args: Record<string, unknown>): QaqcFilter | string {
  const base = args.reset === true ? EMPTY_FILTER : current;
  const patch: Record<string, unknown> = {};
  for (const key of ["labs", "batches", "crm", "types"] as const) {
    if (args[key] === undefined) continue;
    const list = listArg(args[key]);
    if (typeof list === "string") return `Параметр «${key}»: ${list}`;
    if (key === "types") {
      const bad = list.find((t) => !(QAQC_TYPES as readonly string[]).includes(t.toUpperCase()));
      if (bad) return `Тип «${bad}» неизвестен: допустимы ${QAQC_TYPES.join(", ")}`;
    }
    patch[key] = list;
  }
  for (const key of ["date_from", "date_to"] as const) {
    const v = args[key];
    if (v === undefined) continue;
    if (v === null || (isText(v) && !v.trim())) patch[key] = null;
    else {
      const d = parseDate(v);
      if (!d) return `Дата «${String(v)}» не разобрана: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ`;
      patch[key] = d;
    }
  }
  if (args.hole_prefix !== undefined) patch.hole_prefix = isText(args.hole_prefix) ? args.hole_prefix : null;
  if (args.where !== undefined) {
    let raw: unknown = args.where;
    if (isText(raw)) {
      if (!raw.trim()) raw = [];
      else
        try {
          raw = JSON.parse(raw);
        } catch {
          return "Условия where не разобраны: ожидается JSON-массив [{\"field\", \"op\", \"value\"}]";
        }
    }
    if (!Array.isArray(raw)) return "Условия where — JSON-массив";
    if (raw.length > MAX_WHERE) return `Условий «любое поле» не больше ${MAX_WHERE}`;
    const where: WhereCond[] = [];
    for (const c of raw) {
      const checked = checkWhere(c);
      if (typeof checked === "string") return checked;
      where.push(checked);
    }
    patch.where = where;
  }
  const next = normalizeFilter({ ...base, ...patch });
  if (next.date_from && next.date_to && next.date_from > next.date_to) return `Дата «с» (${dateTitle(next.date_from)}) позже даты «по» (${dateTitle(next.date_to)})`;
  return next;
}

const count = (n: number) => n.toLocaleString("ru-RU");

/** «записей N из M»: after the filter of all the records of the data; unknown yet — «…». */
export function countLabel(filtered: number | null | undefined, rows: number | null | undefined): string {
  const known = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);
  return `записей ${known(filtered) ? count(filtered) : "…"} из ${known(rows) ? count(rows) : "…"}`;
}

const few = (list: readonly string[], max = 3) => (list.length <= max ? list.join(", ") : `${list.slice(0, max).join(", ")} и ещё ${list.length - max}`);

/** The value of a condition for people. */
function valueTitle(c: WhereCond): string {
  if (c.op === "empty") return "";
  if (Array.isArray(c.value)) return c.op === "between" ? ` ${c.value[0]} — ${c.value[1]}` : ` ${few(c.value.map(String))}`;
  return ` ${String(c.value)}`;
}

/** The conditions in one line (the answer of the command, the tooltip of the counter); none — «без фильтра». */
export function filterSummary(f: QaqcFilter): string {
  const parts: string[] = [];
  if (f.labs.length) parts.push(`лаборатории: ${few(f.labs)}`);
  if (f.batches.length) parts.push(`партии: ${few(f.batches)}`);
  if (f.date_from || f.date_to) parts.push(`даты: ${f.date_from ? `с ${dateTitle(f.date_from)}` : ""}${f.date_from && f.date_to ? " " : ""}${f.date_to ? `по ${dateTitle(f.date_to)}` : ""}`);
  if (f.crm.length) parts.push(`CRM: ${few(f.crm)}`);
  if (f.types.length) parts.push(`типы: ${f.types.join(", ")}`);
  if (f.hole_prefix) parts.push(`скважины на «${f.hole_prefix}»`);
  for (const c of f.where) parts.push(`${c.field} ${OP_TITLES[c.op]}${valueTitle(c)}`);
  return parts.length ? parts.join("; ") : "без фильтра";
}
