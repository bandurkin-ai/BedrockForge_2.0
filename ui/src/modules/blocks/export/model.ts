/**
 * Export of a block model to a file (ТЗ 8.7.7, 7.35 Д-17; ADR 0023 §12, решение 20; docs/progress/e17-api.md §11): the
 * formats (Micromine DAT, CSV, Parquet, OMF v1) with their options as the core takes them (`POST /blocks/{id}/export`:
 * the fields, the filter of the library and the quick restrictor, the header of Micromine, the encoding, the delimiter,
 * the decimal mark, the code of «пусто»), the form of the window, the body of the request, the place in the form of
 * every refusal of the core, the words about the file written. Datamine DM is not written (the formats of Д-17).
 * A gap is never a zero: an empty value is written as nothing, or as the code the user typed. No React.
 */
import { parseNumber } from "../../importing/numbers";
import { checkDraft, EMPTY_DRAFT, ruleToDraft, type Draft, type Rule } from "../filters/constructor";

export type Format = "dat" | "csv" | "parquet" | "omf";
export type SaveKind = "dat" | "csv" | "parquet" | "omf";

export interface FormatInfo {
  id: Format;
  title: string;
  ext: string;
  /** the filter of the save window */
  filter: SaveKind;
  note: string;
}

export const FORMATS: readonly FormatInfo[] = [
  {
    id: "dat",
    title: "Micromine DAT",
    ext: ".dat",
    filter: "dat",
    note: "Столбцы X, Y, Z (центр блока в осях проекта), DX, DY, DZ (размер по осям модели) и поля. Имена полей — до 10 знаков, сетка модели — в шапке файла. Субблоки пишутся как есть: строка — блок.",
  },
  {
    id: "csv",
    title: "CSV",
    ext: ".csv",
    filter: "csv",
    note: "Столбцы X, Y, Z, DX, DY, DZ и поля. Заголовок Micromine — три строки: имена, типы и сетка (у повёрнутой модели и модели с переменным шагом строка сетки не пишется).",
  },
  {
    id: "parquet",
    title: "Apache Parquet",
    ext: ".parquet",
    filter: "parquet",
    note: "Столбцы idx (номер родителя), у модели с субблоками — sx…wz, затем X, Y, Z, DX, DY, DZ и поля; сетка и поля — в метаданных bf2.block_model. Пропуск — пустое значение.",
  },
  {
    id: "omf",
    title: "OMF v1 (Leapfrog OMF 0.9)",
    ext: ".omf",
    filter: "omf",
    note: "Регулярная модель, повёрнутая — через оси axis_u/v/w; нужно хотя бы одно поле. Модель с субблоками — только после регуляризации. Блок, не прошедший отбор, — пустая ячейка.",
  },
];

export const formatInfo = (id: Format): FormatInfo => FORMATS.find((f) => f.id === id) ?? FORMATS[0];

export const DAT_ENCODINGS: readonly string[] = ["cp1251", "bocu1", "utf-8"];
export const CSV_ENCODINGS: readonly string[] = ["utf-8-sig", "utf-8", "cp1251", "cp866", "koi8-r", "utf-16"];
export const ENCODING_TITLES: Record<string, string> = {
  cp1251: "Windows-1251 (кириллица)",
  bocu1: "BOCU-1 (как в Micromine)",
  "utf-8": "UTF-8",
  "utf-8-sig": "UTF-8 с меткой (для Excel)",
  cp866: "DOS-866",
  "koi8-r": "KOI8-R",
  "utf-16": "UTF-16",
};
export const DELIMITERS: readonly { id: Delimiter; title: string }[] = [
  { id: ";", title: "точка с запятой" },
  { id: ",", title: "запятая" },
  { id: "\t", title: "табуляция" },
  { id: "|", title: "вертикальная черта" },
];
export type Delimiter = ";" | "," | "\t" | "|";
export type Decimal = "," | ".";

export const encodingsOf = (format: Format): readonly string[] => (format === "dat" ? DAT_ENCODINGS : format === "csv" ? CSV_ENCODINGS : []);
export const defaultEncoding = (format: Format): string => (format === "dat" ? "cp1251" : format === "csv" ? "utf-8-sig" : "");
/** The formats that write the code of «пусто» in place of a gap. */
export const takesEmptyCode = (format: Format): boolean => format === "dat" || format === "csv";

export type FormField = "model" | "format" | "path" | "fields" | "filter" | "restrict" | "header" | "encoding" | "delimiter" | "decimal" | "empty" | "general";

export interface FormIssue {
  field: FormField;
  message: string;
  hint: string;
}

const issue = (field: FormField, message: string, hint: string): FormIssue => ({ field, message, hint });

export interface ExportForm {
  modelId: number | null;
  format: Format;
  /** the file the user chose in the save window, with the format it was chosen for */
  path: string | null;
  pathFormat: Format | null;
  /** every field of the model, in its order; false — only `fields` (in the order of the list) */
  allFields: boolean;
  fields: string[];
  filterId: number | null;
  restrictOn: boolean;
  restrict: Draft;
  header: "plain" | "micromine";
  /** "" — the encoding of the format */
  encoding: string;
  delimiter: Delimiter;
  decimal: Decimal;
  emptyOn: boolean;
  /** the code as typed: «-99» */
  empty: string;
}

export const EMPTY_FORM: ExportForm = {
  modelId: null,
  format: "dat",
  path: null,
  pathFormat: null,
  allFields: true,
  fields: [],
  filterId: null,
  restrictOn: false,
  restrict: EMPTY_DRAFT,
  header: "plain",
  encoding: "",
  delimiter: ";",
  decimal: ",",
  emptyOn: false,
  empty: "-99",
};

/** The file chosen, if it was chosen for this format (another format — another extension — the file is chosen again). */
export const chosenPath = (f: ExportForm): string | null => (f.path && f.pathFormat === f.format ? f.path : null);

/** «Модель.dat»: the name of the model with the extension the format adds, no characters a file name cannot have. */
export function defaultFileName(model: string | null, format: Format): string {
  const base = (model ?? "блочная-модель").replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").trim().replace(/[. ]+$/, "") || "блочная-модель";
  return `${base}${formatInfo(format).ext}`;
}

/** Another format: the encoding the new format has not is dropped (the file is chosen again — see `chosenPath`). */
export const nextFormat = (f: ExportForm, format: Format): ExportForm => ({ ...f, format, encoding: encodingsOf(format).includes(f.encoding) ? f.encoding : "" });

// ---- the body of the request ----------------------------------------------------------------------------------------

/** The body of `POST /blocks/{id}/export`; a key that is absent is the default of the core. */
export interface ExportBody {
  format: Format;
  path: string;
  fields?: string[];
  filter_id?: number;
  restrict?: Rule;
  header?: "micromine";
  encoding?: string;
  delimiter?: Delimiter;
  decimal?: Decimal;
  empty?: number;
  overwrite?: boolean;
}

/** The code of «пусто» as typed; null — not a number (empty is not a number either: a gap is never a zero). */
export function parseEmptyCode(text: string): number | null {
  const v = parseNumber(text);
  return typeof v === "number" ? v : null;
}

/**
 * The body of the form: only what the user changed from the defaults of the core. A wrong form is the first wrong thing
 * in its place (nothing wrong reaches the core): no model, no file, no field chosen, a delimiter equal to the decimal
 * mark, a code of «пусто» that is no number, a wrong restrictor.
 */
export function bodyOfForm(f: ExportForm, fieldNames: readonly string[] | null = null): { ok: true; body: ExportBody } | { ok: false; issue: FormIssue } {
  if (f.modelId === null) return { ok: false, issue: issue("model", "Не выбрана блочная модель.", "Выберите модель в списке.") };
  const path = chosenPath(f);
  if (!path) return { ok: false, issue: issue("path", "Не выбран файл.", "Нажмите «Выбрать файл…» и укажите, куда сохранить модель.") };
  const body: ExportBody = { format: f.format, path };
  if (!f.allFields) {
    if (!f.fields.length) {
      const why = f.format === "omf" ? "Для OMF выберите хотя бы одно поле." : "Отметьте поля или выберите «Все поля».";
      return { ok: false, issue: issue("fields", "Не выбрано ни одного поля.", why) };
    }
    const known = new Set((fieldNames ?? f.fields).map((n) => n.toLowerCase()));
    const unknown = f.fields.filter((n) => !known.has(n.toLowerCase()));
    if (unknown.length) {
      const names = (fieldNames ?? []).join(", ");
      return { ok: false, issue: issue("fields", `В модели нет ${unknown.length > 1 ? "полей" : "поля"} «${unknown.join("», «")}».`, names ? `Поля модели: ${names}.` : "Обновите свойства модели.") };
    }
    body.fields = f.fields;
  } else if (f.format === "omf" && fieldNames !== null && fieldNames.length === 0) {
    // the model has no field: OMF has nothing to write
    return { ok: false, issue: issue("fields", "У модели нет полей: в OMF писать нечего.", "Выберите другой формат: DAT, CSV или Parquet.") };
  }
  if (f.filterId !== null) body.filter_id = f.filterId;
  if (f.restrictOn) {
    const c = checkDraft(f.restrict);
    if (!c.ok) return { ok: false, issue: issue("restrict", c.issue.message, c.issue.hint) };
    body.restrict = c.rule;
  }
  if (f.format === "csv") {
    if (f.header === "micromine") body.header = "micromine";
    if (f.delimiter !== ";") body.delimiter = f.delimiter;
    if (f.decimal !== ",") body.decimal = f.decimal;
    if (f.decimal === f.delimiter) return { ok: false, issue: issue("decimal", "Десятичный знак совпадает с разделителем.", "Выберите разделитель «;» или табуляцию, либо десятичную точку.") };
  }
  if (f.encoding && encodingsOf(f.format).includes(f.encoding)) body.encoding = f.encoding;
  if (takesEmptyCode(f.format) && f.emptyOn) {
    const code = parseEmptyCode(f.empty);
    if (code === null) return { ok: false, issue: issue("empty", `«${f.empty.trim()}» — не число.`, "Введите код пустого числом, например −99, или оставьте пропуски пустыми.") };
    body.empty = code;
  }
  return { ok: true, body };
}

// ---- the refusals of the core ---------------------------------------------------------------------------------------

/** The place in the form a refusal of the core belongs to (by `param`, then by the code). */
export function fieldOfRefusal(code: string, param: string | null, form: ExportForm): FormField {
  const byParam: Record<string, FormField> = { path: "path", format: "format", fields: "fields", filter_id: "filter", header: "header", encoding: "encoding", delimiter: "delimiter", decimal: "decimal", empty: "empty" };
  if (param && param in byParam) return byParam[param];
  if (param?.startsWith("restrict") || param?.startsWith("rules")) return "restrict";
  switch (code) {
    case "files_exist":
    case "bad_path":
      return "path";
    case "omf_subblocks":
    case "omf_not_exportable":
      return "format";
    case "block_model_not_found":
      return "model";
    case "block_field_not_found":
      return "fields";
    case "filter_not_found":
      return "filter";
    case "filter_field_missing":
      return form.restrictOn && form.filterId === null ? "restrict" : "filter";
    case "export_encoding":
    case "export_label_too_long":
      return "encoding";
    case "empty_code_taken":
      return "empty";
    case "export_empty":
      return form.filterId !== null ? "filter" : form.restrictOn ? "restrict" : "general";
    default:
      return "general";
  }
}

export interface Refusal {
  code: string;
  message: string;
  hint: string;
  param: string | null;
}

export function issueOfRefusal(r: Refusal, form: ExportForm): FormIssue {
  return { field: fieldOfRefusal(r.code, r.param, form), message: r.message, hint: r.hint };
}

// ---- the result ------------------------------------------------------------------------------------------------------

/** `result` of the job `blocks.export`. */
export interface ExportResult {
  path: string;
  format: Format;
  rows: number;
  total: number;
  /** null — the core did not tell (a size is not guessed) */
  bytes: number | null;
  warnings: string[];
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function readResult(raw: unknown): ExportResult | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.path !== "string" || !isNum(r.rows) || !isNum(r.total)) return null;
  const format = FORMATS.find((f) => f.id === r.format)?.id;
  if (!format) return null;
  return { path: r.path, format, rows: r.rows, total: r.total, bytes: isNum(r.bytes) ? r.bytes : null, warnings: Array.isArray(r.warnings) ? r.warnings.filter((w): w is string => typeof w === "string") : [] };
}

const n = (v: number): string => v.toLocaleString("ru-RU");

export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${n(bytes)} Б`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} КБ`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ`;
  return `${(bytes / 1024 ** 3).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ГБ`;
}

/** «Модель «Руда» записана в DAT: блоков 350 из 350 (31,5 КБ) — D:\out\model.dat». A filter that left some out is said. */
export function resultText(model: string, r: ExportResult): string {
  const how = r.rows === r.total ? `блоков ${n(r.rows)}` : `блоков ${n(r.rows)} из ${n(r.total)} (остальные не прошли отбор)`;
  const size = r.bytes === null ? "" : ` (${sizeText(r.bytes)})`;
  return `Модель «${model}» записана в ${formatInfo(r.format).title}: ${how}${size} — ${r.path}`;
}

/** What the filter and the restrictor leave of the blocks: «Записано 120 из 350 блоков». */
export function selectionText(r: ExportResult): string | null {
  return r.rows === r.total ? null : `Записано ${n(r.rows)} из ${n(r.total)} блоков: остальные не прошли фильтр или ограничитель.`;
}

/** Why OMF cannot be chosen for the model before anything is asked of the core; null — it can. */
export function omfReason(sub: boolean | undefined, name: string): string | null {
  return sub ? `Блочная модель «${name}» с субблоками: OMF не хранит субблоки — экспорт возможен только после регуляризации.` : null;
}

export const draftOf = (rule: Rule): Draft => ruleToDraft(rule);
