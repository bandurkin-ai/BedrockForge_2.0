/**
 * The mapping of the columns of a source to the records of QAQC (ADR 0020 §2; ТЗ 8.4.1): automatic choice by the
 * names of the columns (SAMPLE_ID, HOLE, TYPE, PARENT_ID, CRM_ID, LAB, BATCH, DATE, SEQ; the other numeric columns
 * are elements, the unit from the name «AU_ppm», «Cu, %» or by hand), the check of the required fields before the
 * job, the field a refusal of the core belongs to, the totals of a finished import. Pure.
 */
import { CoreError } from "../../../shell/api";
import { mappingFromCore, type FieldKey, type ImportCounts, type InspectAnswer, type InspectColumn, type Mapping, type MappedElement } from "../setupApi";

export interface FieldSpec {
  key: FieldKey;
  title: string;
  required: boolean;
  hint: string;
}

export const FIELDS: FieldSpec[] = [
  { key: "sample_id", title: "SAMPLE_ID — номер пробы", required: true, hint: "уникальный номер пробы" },
  { key: "type", title: "TYPE — тип пробы", required: true, hint: "код типа: ORIG, CRM, BLANK, DUP_F, DUP_C, DUP_P, UMP или код файла" },
  { key: "hole", title: "HOLE — скважина", required: false, hint: "фильтр «префикс скважины»" },
  { key: "parent_id", title: "PARENT_ID — исходная проба", required: false, hint: "у дубликатов и внешнего контроля; без него пара не образуется" },
  { key: "crm_id", title: "CRM_ID — стандарт", required: false, hint: "у проб CRM" },
  { key: "lab", title: "LAB — лаборатория", required: false, hint: "" },
  { key: "batch", title: "BATCH — партия", required: false, hint: "без партии нет статуса партии и предыдущей пробы" },
  { key: "date", title: "DATE — дата анализа", required: false, hint: "ГГГГ-ММ-ДД, ДД.ММ.ГГГГ, ДД/ММ/ГГГГ или дата Excel" },
  { key: "seq", title: "SEQ — номер в партии", required: false, hint: "порядок проб в партии" },
];

/** Names a column may carry for a field: compared after `normName`. */
const SYNONYMS: Record<FieldKey, string[]> = {
  sample_id: ["sample_id", "sampleid", "sample", "sample_no", "sample_number", "samp_id", "id_пробы", "номер_пробы", "проба", "пробы", "no_пробы"],
  hole: ["hole", "hole_id", "holeid", "bhid", "dhid", "скважина", "скв", "выработка"],
  type: ["type", "qc_type", "qctype", "sample_type", "qaqc_type", "control_type", "тип", "тип_пробы"],
  parent_id: ["parent_id", "parentid", "parent", "orig_id", "original_id", "original", "исходная", "исходная_проба", "родитель"],
  crm_id: ["crm_id", "crmid", "crm", "standard", "standard_id", "std_id", "std", "стандарт", "со"],
  lab: ["lab", "lab_id", "laboratory", "лаборатория", "лаб"],
  batch: ["batch", "batch_id", "batchid", "job", "job_no", "work_order", "партия", "заказ"],
  date: ["date", "assay_date", "analysis_date", "date_analysed", "дата", "дата_анализа"],
  seq: ["seq", "sequence", "seq_no", "order", "position", "порядок", "номер_в_партии", "п_п"],
};

/** «Sample ID» → «sample_id»; «№ пробы» → «no_пробы». */
export function normName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/№/g, "no_")
    .replace(/[\s.\-/]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "");
}

/** Known spellings of units (ADR 0020 §3: г/т = ppm, ppb, % are recalculated). */
const UNITS: [RegExp, string][] = [
  [/^(ppm|г_т|g_t|gpt|г\/т|g\/t)$/i, "г/т"],
  [/^ppb$/i, "ppb"],
  [/^(%|pct|perc|percent|проц)$/i, "%"],
];

const unitOf = (token: string): string | null => {
  const t = token.trim().replace(/[()[\]]/g, "").replace(/\s+/g, "");
  for (const [re, unit] of UNITS) if (re.test(t)) return unit === "г/т" && /ppm/i.test(t) ? "ppm" : unit;
  return null;
};

/** «AU_ppm» → {name: «AU», unit: «ppm»}; «Cu, %» → {Cu, %}; «Au (g/t)» → {Au, г/т}; «Ag» → {Ag, ""}. */
export function elementOfName(column: string): { name: string; unit: string } {
  const c = column.trim();
  const m = /^(.*?)[\s_,]*[([]?\s*(ppm|ppb|pct|perc|percent|%|g\/t|г\/т|gpt|g_t|г_т)\s*[)\]]?$/i.exec(c);
  if (m && m[1].trim()) {
    const unit = unitOf(m[2]);
    if (unit) return { name: m[1].trim().replace(/[\s_,]+$/, ""), unit };
  }
  return { name: c, unit: "" };
}

/** A column of detection limits: «DL_Au», «Au_DL», «Au DL» → «Au». */
function dlElementOf(column: string): string | null {
  const n = column.trim();
  const m = /^(?:dl|по|lod)[\s_]+(.+)$/i.exec(n) ?? /^(.+?)[\s_]+(?:dl|по|lod)$/i.exec(n);
  return m ? elementOfName(m[1]).name : null;
}

/** The automatic mapping of the columns (the core's own choice, when it gives one, is used instead). */
export function autoMapping(columns: readonly InspectColumn[]): Mapping {
  const fields: Partial<Record<FieldKey, string>> = {};
  const used = new Set<string>();
  for (const f of FIELDS) {
    const syn = SYNONYMS[f.key];
    const col = columns.find((c) => !used.has(c.name) && syn.includes(normName(c.name)));
    if (col) {
      fields[f.key] = col.name;
      used.add(col.name);
    }
  }
  const dlCols = columns.filter((c) => !used.has(c.name) && c.kind === "number" && dlElementOf(c.name) !== null);
  for (const c of dlCols) used.add(c.name);
  const elements: MappedElement[] = columns
    .filter((c) => !used.has(c.name) && c.kind === "number")
    .map((c) => {
      const e = elementOfName(c.name);
      const dl = dlCols.find((d) => dlElementOf(d.name)?.toLowerCase() === e.name.toLowerCase());
      return { column: c.name, name: e.name, unit: e.unit, ...(dl ? { dl: dl.name } : {}) };
    });
  for (const e of elements) used.add(e.column);
  const extra = columns.filter((c) => !used.has(c.name)).map((c) => c.name).slice(0, MAX_EXTRA);
  return { fields, elements, extra };
}

export const MAX_EXTRA = 40;
export const MAX_ELEMENTS = 60;

/** The mapping the form starts with: the core's, else the automatic one; columns that vanished are dropped. */
export function startMapping(answer: Pick<InspectAnswer, "columns" | "mapping">): Mapping {
  const names = new Set(answer.columns.map((c) => c.name));
  const given = answer.mapping;
  if (!given) return autoMapping(answer.columns);
  const fields = Object.fromEntries(Object.entries(given.fields ?? {}).filter(([, col]) => col && names.has(col))) as Mapping["fields"];
  const elements = (given.elements ?? []).filter((e) => names.has(e.column)).map((e) => ({ ...e, unit: e.unit ?? "", dl: e.dl && names.has(e.dl) ? e.dl : null }));
  return { fields, elements, extra: (given.extra ?? []).filter((c) => names.has(c)) };
}

/** A problem of the form at its place: a field, «elements», an element by its column, «name». */
export interface Problem {
  at: string;
  message: string;
}

/** Why the mapping cannot be sent (the same refusals as the core's 422 `no_sample_id`, `no_type`, `no_elements`). */
export function mappingProblems(m: Mapping, name: string): Problem[] {
  const out: Problem[] = [];
  if (!name.trim()) out.push({ at: "name", message: "Задайте название данных" });
  if (!m.fields.sample_id) out.push({ at: "sample_id", message: "Выберите колонку номера пробы (SAMPLE_ID)" });
  if (!m.fields.type) out.push({ at: "type", message: "Выберите колонку типа пробы (TYPE)" });
  if (!m.elements.length) out.push({ at: "elements", message: "Отметьте хотя бы один элемент" });
  if (m.elements.length > MAX_ELEMENTS) out.push({ at: "elements", message: `Не больше ${MAX_ELEMENTS} элементов` });
  if (m.extra.length > MAX_EXTRA) out.push({ at: "extra", message: `Не больше ${MAX_EXTRA} прочих полей` });
  const seen = new Map<string, string>();
  for (const [key, col] of Object.entries(m.fields)) {
    if (!col) continue;
    const before = seen.get(col);
    if (before) out.push({ at: key, message: `Колонка «${col}» уже выбрана для ${before.toUpperCase()}` });
    else seen.set(col, key);
  }
  const names = new Set<string>();
  for (const e of m.elements) {
    if (seen.has(e.column)) out.push({ at: `el:${e.column}`, message: `Колонка «${e.column}» уже выбрана для ${String(seen.get(e.column)).toUpperCase()}` });
    const n = e.name.trim();
    if (!n) out.push({ at: `el:${e.column}`, message: "Задайте имя элемента" });
    else if (names.has(n.toLowerCase())) out.push({ at: `el:${e.column}`, message: `Элемент «${n}» уже есть` });
    names.add(n.toLowerCase());
  }
  return out;
}

export const problemAt = (problems: readonly Problem[], at: string): string | null => problems.find((p) => p.at === at)?.message ?? null;

/** Where the form shows a refusal of the core: the field it names (`param`, or the code of a missing field). */
export function errorPlace(e: unknown): string | null {
  if (!(e instanceof CoreError)) return null;
  const byCode: Record<string, string> = { no_sample_id: "sample_id", no_type: "type", no_elements: "elements", name_taken: "name", too_many_rows: "source" };
  if (byCode[e.code]) return byCode[e.code];
  const param = e.body?.param;
  if (typeof param !== "string" || !param) return null;
  const p = param.replace(/^mapping\./, "").replace(/^fields\./, "");
  return p === "name" || FIELDS.some((f) => f.key === p) ? p : p.startsWith("elements") ? "elements" : p.startsWith("extra") ? "extra" : "source";
}

/** Turns a column on or off as an element (the unit from its name). */
export function toggleElement(m: Mapping, column: string, on: boolean): Mapping {
  if (!on) return { ...m, elements: m.elements.filter((e) => e.column !== column) };
  if (m.elements.some((e) => e.column === column)) return m;
  const e = elementOfName(column);
  return { ...m, elements: [...m.elements, { column, name: e.name, unit: e.unit }], extra: m.extra.filter((c) => c !== column) };
}

export function setField(m: Mapping, key: FieldKey, column: string): Mapping {
  const fields = { ...m.fields };
  if (column) fields[key] = column;
  else delete fields[key];
  return { ...m, fields, elements: column ? m.elements.filter((e) => e.column !== column) : m.elements, extra: column ? m.extra.filter((c) => c !== column) : m.extra };
}

export const patchElement = (m: Mapping, column: string, patch: Partial<MappedElement>): Mapping => ({ ...m, elements: m.elements.map((e) => (e.column === column ? { ...e, ...patch } : e)) });

/**
 * A mapping given to `qaqc.import` as JSON text (the assistant, the form) — the form of the core
 * (`normalize.parse_mapping`): the roles at the top `{sample_id, type, hole?, parent_id?, crm_id?, lab?, batch?,
 * date?, seq?}` (a column or null), `elements: [{column, name?, unit?, dl_column?}]` (no name or no unit — from the
 * name of the column, as the core does), `extra: [column…]`, and `read: {sheet?, encoding?, delimiter?,
 * header_row?}` of a file (taken apart by the binding). A string — why it is not one.
 */
export function mappingFromArg(value: unknown): Mapping | string {
  let raw: unknown = value;
  if (typeof value === "string") {
    try {
      raw = JSON.parse(value);
    } catch {
      return "mapping — не JSON-объект";
    }
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return "mapping — не JSON-объект";
  const r = raw as Record<string, unknown>;
  if ("fields" in r) return "колонки ролей указываются на верхнем уровне (sample_id, type, …), без «fields»";
  for (const f of FIELDS) if (r[f.key] !== undefined && r[f.key] !== null && typeof r[f.key] !== "string") return `${f.key} — имя колонки или null`;
  if (r.elements !== undefined && !Array.isArray(r.elements)) return "elements — список объектов {column, name?, unit?, dl_column?}";
  const items = (r.elements ?? []) as unknown[];
  for (const [i, e] of items.entries()) {
    if (!e || typeof e !== "object" || Array.isArray(e) || typeof (e as { column?: unknown }).column !== "string") return `elements[${i}] — объект {column, name?, unit?, dl_column?}`;
  }
  if (r.extra !== undefined && !Array.isArray(r.extra)) return "extra — список колонок";
  const m = mappingFromCore(r) ?? { fields: {}, elements: [], extra: [] };
  // as the core: no name or no unit key — from the name of the column («AU_ppm» → AU, ppm)
  const elements: MappedElement[] = m.elements.map((e, i) => {
    const x = items[i] as Record<string, unknown>;
    const auto = elementOfName(e.column);
    return { ...e, name: typeof x.name === "string" && x.name.trim() ? x.name.trim() : auto.name, unit: "unit" in x ? e.unit : auto.unit };
  });
  return { ...m, elements };
}

/** Fills the parts of a mapping the assistant left out from the automatic choice. */
export function completeMapping(given: Mapping, columns: readonly InspectColumn[]): Mapping {
  const auto = autoMapping(columns);
  const fields = Object.keys(given.fields).length ? given.fields : auto.fields;
  const elements = given.elements.length ? given.elements : auto.elements.filter((e) => !Object.values(fields).includes(e.column));
  return { fields, elements, extra: given.extra.length ? given.extra : auto.extra.filter((c) => !Object.values(fields).includes(c)) };
}

const COUNT_TITLES: [keyof ImportCounts, string][] = [
  ["no_parent", "исходная проба не найдена"],
  ["dup_ids", "повторы SAMPLE_ID"],
  ["bad_dates", "неразобранные даты"],
  ["bad_numbers", "нечисловые значения"],
  ["below_dl", "ниже предела обнаружения"],
  ["above_dl", "выше предела измерения"],
];

/** The totals of a finished import: «Записей: 1 200» and the non-zero counters. */
export function countLines(rows: number, counts: ImportCounts | undefined): string[] {
  const out = [`Записей: ${rows.toLocaleString("ru-RU")}`];
  for (const [k, title] of COUNT_TITLES) {
    const n = counts?.[k];
    if (typeof n === "number" && n > 0) out.push(`${title}: ${n.toLocaleString("ru-RU")}`);
  }
  return out;
}

/** The value of a preview row for a column (rows by name or in the order of the columns); missing — «». */
export function cellOf(row: Record<string, unknown> | unknown[], columns: readonly InspectColumn[], column: string): string {
  const v = Array.isArray(row) ? row[columns.findIndex((c) => c.name === column)] : row[column];
  return v === null || v === undefined || (typeof v === "number" && Number.isNaN(v)) ? "" : String(v);
}

/** «Данные QAQC» + the name of the file without its extension, or of the table. */
export function defaultDataName(source: { kind: "file"; path: string } | { kind: "table"; tableName: string; setName: string }): string {
  if (source.kind === "table") return `${source.setName} · ${source.tableName}`;
  const base = source.path.split(/[\\/]/).pop() ?? source.path;
  return base.replace(/\.[^.]+$/, "") || "Данные QAQC";
}
