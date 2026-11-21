/**
 * The forms of «Настройка QAQC» (ADR 0020 §3; ТЗ 8.4.2 — the ranges of the thresholds): «Пороги» with the ranges
 * of the ТЗ and «По умолчанию», «Типы» (codes of the files → types; an unrecognised code is never ORIG silently),
 * «CRM» (μ > 0, σ > 0, the key CRM_ID × element without case, ≤ 5000 rows), «DL» (element × laboratory or
 * «любая»). The arguments of `qaqc.config` and `qaqc.crm` are merged over the stored values here. Pure.
 */
import { numberText, parseNumber } from "../../composites/params";
import { QAQC_TYPES, type CrmRow, type DlRow, type Limits, type QaqcConfig, type QaqcData, type QaqcType } from "../setupApi";

// ---- «Пороги» ----

export interface LimitSpec {
  key: keyof Limits;
  title: string;
  group: string;
  min: number;
  max: number;
  integer?: boolean;
  odd?: boolean;
  unit?: string;
}

export const LIMIT_SPECS: LimitSpec[] = [
  { key: "sigma_warn", title: "Предупреждение, |z| больше", group: "Стандарты", min: 1, max: 5, unit: "σ" },
  { key: "sigma_fail", title: "Отказ, |z| больше", group: "Стандарты", min: 1, max: 5, unit: "σ" },
  { key: "run", title: "Серия по одну сторону μ", group: "Стандарты", min: 5, max: 20, integer: true, unit: "проб" },
  { key: "k_blank", title: "Порог бланка k_б", group: "Бланки", min: 1, max: 50, unit: "× DL" },
  { key: "k_dup", title: "Отсев пар дубликатов k_д", group: "Дубликаты", min: 0, max: 50, unit: "× DL" },
  { key: "th_group", title: "Группа Томпсона–Ховарта", group: "Дубликаты", min: 5, max: 51, integer: true, odd: true, unit: "пар" },
  { key: "hard_p", title: "Критерий HARD, пульповые", group: "Дубликаты", min: 1, max: 100, unit: "%" },
  { key: "hard_c", title: "Критерий HARD, дроблёные", group: "Дубликаты", min: 1, max: 100, unit: "%" },
  { key: "hard_f", title: "Критерий HARD, полевые", group: "Дубликаты", min: 1, max: 100, unit: "%" },
  { key: "hard_ump", title: "Критерий HARD, внешний контроль", group: "Внешний контроль", min: 1, max: 100, unit: "%" },
  { key: "hard_share", title: "Доля пар в пределах критерия", group: "Дубликаты", min: 50, max: 100, unit: "%" },
  { key: "n_boot", title: "Реализаций бутстрапа RMA", group: "Внешний контроль", min: 100, max: 5000, integer: true },
  { key: "seed", title: "Зерно бутстрапа", group: "Внешний контроль", min: 0, max: 2147483647, integer: true },
];

export const DEFAULT_LIMITS: Limits = { sigma_warn: 2, sigma_fail: 3, run: 9, k_blank: 5, k_dup: 5, th_group: 11, hard_p: 10, hard_c: 20, hard_f: 30, hard_ump: 10, hard_share: 90, n_boot: 1000, seed: 12345 };

export const LIMIT_KEYS = LIMIT_SPECS.map((s) => s.key);

export type LimitTexts = Record<keyof Limits, string>;

export const limitsText = (l: Limits): LimitTexts => Object.fromEntries(LIMIT_SPECS.map((s) => [s.key, numberText(l[s.key] ?? DEFAULT_LIMITS[s.key])])) as LimitTexts;

/** «1–5 σ», «5–51, нечётное». */
export function rangeOf(s: LimitSpec): string {
  return `${numberText(s.min)}–${numberText(s.max)}${s.unit ? ` ${s.unit}` : ""}${s.odd ? ", нечётное" : s.integer ? ", целое" : ""}`;
}

/** Why one value is out of its range (ADR 0020 §3; the core answers the same with 422 `bad_param`); null — fits. */
export function limitError(s: LimitSpec, v: number | null): string | null {
  if (v === null || !Number.isFinite(v)) return "Введите число";
  if (s.integer && !Number.isInteger(v)) return `Целое число: ${rangeOf(s)}`;
  if (v < s.min - 1e-9 || v > s.max + 1e-9) return `Вне диапазона: ${rangeOf(s)}`;
  if (s.odd && v % 2 === 0) return `Нечётное число: ${rangeOf(s)}`;
  return null;
}

const SIGMA_ORDER = "Порог отказа должен быть больше порога предупреждения";

/** The thresholds of the form: the values when every one fits, the errors by key. */
export function checkLimits(t: LimitTexts): { values: Limits | null; errors: Partial<Record<keyof Limits, string>> } {
  const errors: Partial<Record<keyof Limits, string>> = {};
  const values = { ...DEFAULT_LIMITS };
  for (const s of LIMIT_SPECS) {
    const v = parseNumber(t[s.key] ?? "");
    const e = limitError(s, v);
    if (e) errors[s.key] = e;
    else values[s.key] = v as number;
  }
  if (!errors.sigma_warn && !errors.sigma_fail && values.sigma_warn >= values.sigma_fail) errors.sigma_fail = SIGMA_ORDER;
  return { values: Object.keys(errors).length ? null : values, errors };
}

// ---- «Типы» ----

export const normCode = (code: string): string => code.trim().toUpperCase();

/** The default map (ADR 0020 §3): the names of the types themselves, «STD», «STANDARD» → CRM, «BLK» → BLANK. */
export const DEFAULT_TYPES: Record<string, QaqcType> = { ...Object.fromEntries(QAQC_TYPES.map((t) => [t, t])), STD: "CRM", STANDARD: "CRM", BLK: "BLANK" };

const isType = (v: unknown): v is QaqcType => typeof v === "string" && (QAQC_TYPES as readonly string[]).includes(v);

/** The type of a code of a file: the map without case and outer spaces; empty — ORIG when `emptyIsOrig`; else null. */
export function typeOfCode(code: string, types: Record<string, QaqcType>, emptyIsOrig: boolean): QaqcType | null {
  const c = normCode(code);
  if (!c) return emptyIsOrig ? "ORIG" : null;
  for (const [k, t] of Object.entries(types)) if (normCode(k) === c) return t;
  return null;
}

export interface CodeRow {
  /** the code as the files carry it (the first spelling met) */
  code: string;
  n: number;
  type: QaqcType | null;
}

/** The codes of TYPE of every data with their number of records, and the codes of the map no data has (n = 0). */
export function codeRows(data: readonly Pick<QaqcData, "type_codes">[], types: Record<string, QaqcType>, emptyIsOrig: boolean): CodeRow[] {
  const by = new Map<string, CodeRow>();
  for (const d of data)
    for (const c of d.type_codes ?? []) {
      const key = normCode(c.code);
      const row = by.get(key);
      if (row) row.n += c.n;
      else by.set(key, { code: c.code.trim(), n: c.n, type: null });
    }
  for (const k of Object.keys(types)) if (!by.has(normCode(k))) by.set(normCode(k), { code: k.trim(), n: 0, type: null });
  const rows = [...by.values()].map((r) => ({ ...r, type: typeOfCode(r.code, types, emptyIsOrig) }));
  // unrecognised first, then by the number of records
  return rows.sort((a, b) => Number(a.type !== null) - Number(b.type !== null) || b.n - a.n || a.code.localeCompare(b.code, "ru"));
}

/** Gives a code a type (null — takes it out of the map: «не распознано»). */
export function setCodeType(types: Record<string, QaqcType>, code: string, type: QaqcType | null): Record<string, QaqcType> {
  const c = normCode(code);
  const out = Object.fromEntries(Object.entries(types).filter(([k]) => normCode(k) !== c)) as Record<string, QaqcType>;
  if (type && c) out[code.trim()] = type;
  return out;
}

/** The argument `types` of `qaqc.config` from the form: every code of the map, removed ones as null. */
export function typesArg(before: Record<string, QaqcType>, after: Record<string, QaqcType>): Record<string, QaqcType | null> {
  const out: Record<string, QaqcType | null> = { ...after };
  for (const k of Object.keys(before)) if (!Object.keys(after).some((a) => normCode(a) === normCode(k))) out[k] = null;
  return out;
}

// ---- «CRM» ----

export const MAX_CRM = 5000;

export interface CrmText {
  crm_id: string;
  element: string;
  mean: string;
  sd: string;
  unit: string;
  source: string;
}

export const crmKey = (r: { crm_id: string; element: string }): string => `${normCode(r.crm_id)}\u0000${normCode(r.element)}`;

export const crmTexts = (rows: readonly Partial<CrmRow>[]): CrmText[] =>
  rows.map((r) => ({
    crm_id: String(r.crm_id ?? ""),
    element: String(r.element ?? ""),
    mean: typeof r.mean === "number" && Number.isFinite(r.mean) ? numberText(r.mean) : String(r.mean ?? ""),
    sd: typeof r.sd === "number" && Number.isFinite(r.sd) ? numberText(r.sd) : String(r.sd ?? ""),
    unit: String(r.unit ?? ""),
    source: String(r.source ?? ""),
  }));

/** Why a row of the reference is rejected (the same rule as the core's 422 `bad_crm {row}`). */
export function crmRowError(r: CrmText): string | null {
  if (!r.crm_id.trim()) return "Не задан CRM_ID";
  if (!r.element.trim()) return "Не задан элемент";
  const mean = parseNumber(r.mean);
  if (mean === null || mean <= 0) return "μ — число больше 0";
  const sd = parseNumber(r.sd);
  if (sd === null || sd <= 0) return "σ — число больше 0";
  return null;
}

/** The rows of the form: the reference when every row fits, the errors by the index of the row. */
export function checkCrm(rows: readonly CrmText[]): { rows: CrmRow[] | null; errors: Map<number, string>; general: string | null } {
  const errors = new Map<number, string>();
  const seen = new Map<string, number>();
  rows.forEach((r, i) => {
    const e = crmRowError(r);
    if (e) return void errors.set(i, e);
    const k = crmKey(r);
    const before = seen.get(k);
    if (before !== undefined) errors.set(i, `Повтор: ${r.crm_id.trim()} × ${r.element.trim()} уже в строке ${before + 1}`);
    else seen.set(k, i);
  });
  const general = rows.length > MAX_CRM ? `Не больше ${MAX_CRM} строк в справочнике` : null;
  if (errors.size || general) return { rows: null, errors, general };
  const out = rows.map((r) => ({ crm_id: r.crm_id.trim(), element: r.element.trim(), mean: parseNumber(r.mean) as number, sd: parseNumber(r.sd) as number, unit: r.unit.trim(), source: r.source.trim() }));
  return { rows: out, errors, general };
}

/** Rows read from a file go over the form: a row of the same key is replaced, a new one is added at the end. */
export function mergeRead(form: readonly CrmText[], read: readonly Partial<CrmRow>[]): CrmText[] {
  const out = [...form];
  for (const r of crmTexts(read)) {
    const i = out.findIndex((x) => crmKey(x) === crmKey(r));
    if (i >= 0) out[i] = r;
    else out.push(r);
  }
  return out;
}

export interface CrmRemove {
  crm_id: string;
  element?: string;
}

/** The arguments of `qaqc.crm` from the form: every row, and the keys of the stored reference the form has not. */
export function crmArgs(before: readonly CrmRow[], after: readonly CrmRow[]): { rows: CrmRow[]; remove: CrmRemove[] } {
  const keys = new Set(after.map(crmKey));
  return { rows: [...after], remove: before.filter((r) => !keys.has(crmKey(r))).map((r) => ({ crm_id: r.crm_id, element: r.element })) };
}

/** The reference after `qaqc.crm`: the removed keys (without element — every element of the CRM) out, rows upserted. */
export function applyCrm(base: readonly CrmRow[], rows: readonly CrmRow[], remove: readonly CrmRemove[]): CrmRow[] {
  const gone = (r: CrmRow) => remove.some((x) => normCode(x.crm_id) === normCode(r.crm_id) && (x.element === undefined || normCode(x.element) === normCode(r.element)));
  const out = base.filter((r) => !gone(r));
  for (const r of rows) {
    const i = out.findIndex((x) => crmKey(x) === crmKey(r));
    if (i >= 0) out[i] = r;
    else out.push(r);
  }
  return out;
}

// ---- «DL» ----

export interface DlText {
  element: string;
  /** «» — any laboratory */
  lab: string;
  value: string;
  unit: string;
}

export const dlTexts = (rows: readonly DlRow[]): DlText[] => rows.map((r) => ({ element: r.element, lab: r.lab ?? "", value: Number.isFinite(r.value) ? numberText(r.value) : "", unit: r.unit ?? "" }));

export function checkDl(rows: readonly DlText[]): { rows: DlRow[] | null; errors: Map<number, string> } {
  const errors = new Map<number, string>();
  const seen = new Map<string, number>();
  rows.forEach((r, i) => {
    if (!r.element.trim()) return void errors.set(i, "Не задан элемент");
    const v = parseNumber(r.value);
    if (v === null || v <= 0) return void errors.set(i, "DL — число больше 0");
    const k = `${normCode(r.element)}\u0000${normCode(r.lab)}`;
    const before = seen.get(k);
    if (before !== undefined) errors.set(i, `Повтор: ${r.element.trim()} × ${r.lab.trim() || "любая"} уже в строке ${before + 1}`);
    else seen.set(k, i);
  });
  if (errors.size) return { rows: null, errors };
  return { rows: rows.map((r) => ({ element: r.element.trim(), lab: r.lab.trim() || null, value: parseNumber(r.value) as number, unit: r.unit.trim() })), errors };
}

// ---- the arguments of the commands ----

function jsonOf(value: unknown, what: string): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    throw new Error(`${what} — не JSON`);
  }
}

/** `qaqc.config` over the stored setup: `types` merged (null — out), `dl` replaced, thresholds checked. */
export function applyConfigArgs(base: QaqcConfig, args: Record<string, unknown>): Omit<QaqcConfig, "version"> | string {
  try {
    let types = { ...base.types };
    if (args.types !== undefined) {
      const t = jsonOf(args.types, "types");
      if (!t || typeof t !== "object" || Array.isArray(t)) return "types — JSON-объект «код файла → тип»";
      for (const [code, type] of Object.entries(t as Record<string, unknown>)) {
        if (type === null || type === "") types = setCodeType(types, code, null);
        else if (isType(type)) types = setCodeType(types, code, type);
        else return `Тип «${String(type)}» кода «${code}» неизвестен: допустимы ${QAQC_TYPES.join(", ")} или null`;
      }
    }
    let dl = base.dl;
    if (args.dl !== undefined) {
      const raw = jsonOf(args.dl, "dl");
      if (!Array.isArray(raw)) return "dl — JSON-массив [{element, lab, value, unit}]";
      const checked = checkDl(raw.map((r: Record<string, unknown>) => ({ element: String(r?.element ?? ""), lab: r?.lab == null ? "" : String(r.lab), value: r?.value == null ? "" : String(r.value), unit: String(r?.unit ?? "") })));
      if (!checked.rows) {
        const [[i, e]] = [...checked.errors];
        return `DL, строка ${i + 1}: ${e}`;
      }
      dl = checked.rows;
    }
    const limits = { ...DEFAULT_LIMITS, ...base.limits };
    for (const s of LIMIT_SPECS) {
      const v = args[s.key];
      if (v === undefined) continue;
      const e = limitError(s, typeof v === "number" ? v : null);
      if (e) return `${s.title}: ${e}`;
      limits[s.key] = v as number;
    }
    if (limits.sigma_warn >= limits.sigma_fail) return SIGMA_ORDER;
    const emptyIsOrig = typeof args.empty_is_orig === "boolean" ? args.empty_is_orig : base.empty_is_orig;
    return { types, dl, limits, empty_is_orig: emptyIsOrig };
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
}

/** `qaqc.crm` over the stored reference: rows upserted by key, `remove` taken out; each row checked. */
export function applyCrmArgs(base: readonly CrmRow[], args: Record<string, unknown>): CrmRow[] | string {
  try {
    const raw = args.rows === undefined ? [] : jsonOf(args.rows, "rows");
    if (!Array.isArray(raw)) return "rows — JSON-массив [{crm_id, element, mean, sd, unit, source}]";
    const texts = crmTexts(raw as Partial<CrmRow>[]);
    const checked = checkCrm(texts);
    if (!checked.rows) {
      const first = [...checked.errors][0];
      return first ? `CRM, строка ${first[0] + 1}: ${first[1]}` : (checked.general ?? "Справочник не принят");
    }
    const rem = args.remove === undefined ? [] : jsonOf(args.remove, "remove");
    if (!Array.isArray(rem)) return "remove — JSON-массив [{crm_id, element?}]";
    const remove: CrmRemove[] = rem.filter((x): x is Record<string, unknown> => !!x && typeof x === "object").map((x) => ({ crm_id: String(x.crm_id ?? ""), ...(x.element != null ? { element: String(x.element) } : {}) }));
    const out = applyCrm(base, checked.rows, remove);
    return out.length > MAX_CRM ? `Не больше ${MAX_CRM} строк в справочнике` : out;
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
}
