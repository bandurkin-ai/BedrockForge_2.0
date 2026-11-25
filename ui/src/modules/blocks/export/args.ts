/**
 * The arguments of the command `blocks.export` ↔ the form of the window (ТЗ 8.7.7; ADR 0023 §12): the window runs the
 * command with the arguments of its form, a call of the user's key, of Ctrl+K or of the assistant gives the arguments by
 * name; both become the same body of the request. Every wrong argument is told here in Russian with a hint and its place
 * in the form, nothing wrong reaches the core. No React.
 */
import { checkDraft, ruleToDraft, type Rule } from "../filters/constructor";
import {
  bodyOfForm,
  CSV_ENCODINGS,
  DAT_ENCODINGS,
  DELIMITERS,
  EMPTY_FORM,
  formatInfo,
  FORMATS,
  parseEmptyCode,
  type Decimal,
  type Delimiter,
  type ExportBody,
  type ExportForm,
  type Format,
  type FormField,
  type FormIssue,
} from "./model";

export type Args = Record<string, unknown>;

const issue = (field: FormField, message: string, hint: string): FormIssue => ({ field, message, hint });
const given = (a: Args, k: string): boolean => a[k] !== undefined && a[k] !== null && a[k] !== "";

/** A list of names: «AU, DOM» or an array. */
function names(v: unknown): string[] | null {
  if (Array.isArray(v)) return v.every((x) => typeof x === "string") ? (v as string[]).map((x) => x.trim()).filter(Boolean) : null;
  if (typeof v === "string") return v.split(/[,;\n]/).map((x) => x.trim()).filter(Boolean);
  return null;
}

const DELIMITER_WORDS: Record<string, Delimiter> = { ";": ";", ",": ",", "\t": "\t", "|": "|", tab: "\t", табуляция: "\t", "точка с запятой": ";", запятая: ",", "вертикальная черта": "|" };

/** The format of a path by its extension; undefined — not one of the four. */
export function formatOfPath(path: string): Format | undefined {
  const ext = /\.[^.\\/]+$/.exec(path)?.[0].toLowerCase();
  return FORMATS.find((f) => f.ext === ext || (f.id === "csv" && ext === ".txt"))?.id;
}

export function formOfArgs(a: Args): { ok: true; form: ExportForm } | { ok: false; issue: FormIssue } {
  const id = typeof a.model_id === "number" ? a.model_id : typeof a.model_id === "string" ? Number(a.model_id) : NaN;
  if (!Number.isInteger(id) || id < 1) return { ok: false, issue: issue("model", "Не указан номер блочной модели.", "Номера моделей — в разделе «Блочные модели» дерева; укажите model_id.") };
  const path = typeof a.path === "string" ? a.path.trim() : "";
  if (!path) return { ok: false, issue: issue("path", "Не указан файл.", "Укажите path — полный путь файла, например D:\\Выгрузка\\модель.dat.") };
  const asked = typeof a.format === "string" ? a.format.trim().toLowerCase() : "";
  const format = asked ? FORMATS.find((f) => f.id === asked)?.id : (formatOfPath(path) ?? "dat");
  if (!format) return { ok: false, issue: issue("format", `Формат «${asked}» не поддерживается.`, `Допустимо: ${FORMATS.map((f) => f.id).join(", ")}.`) };
  const form: ExportForm = { ...EMPTY_FORM, modelId: id, format, path, pathFormat: format };
  if (given(a, "fields")) {
    const list = names(a.fields);
    if (list === null) return { ok: false, issue: issue("fields", "Поля задаются списком имён.", "Например: AU, DOM — через запятую.") };
    Object.assign(form, { allFields: false, fields: list });
  }
  if (given(a, "filter_id")) {
    const v = Number(a.filter_id);
    if (!Number.isInteger(v) || v < 1) return { ok: false, issue: issue("filter", "Номер фильтра — целое число от 1.", "Номера фильтров — в разделе «Фильтры» дерева.") };
    form.filterId = v;
  }
  if (given(a, "restrict")) {
    try {
      const v: unknown = typeof a.restrict === "string" ? JSON.parse(a.restrict) : a.restrict;
      if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("not a tree");
      Object.assign(form, { restrictOn: true, restrict: ruleToDraft(v as Rule) });
    } catch {
      return { ok: false, issue: issue("restrict", "Ограничитель — не дерево правил в JSON.", 'Пример: {"cond": {"field": "AU", "op": ">", "value": 1}}.') };
    }
  }
  if (given(a, "header")) {
    if (a.header !== "plain" && a.header !== "micromine") return { ok: false, issue: issue("header", "Заголовок: «plain» (одна строка) или «micromine» (три строки).", "Заголовок Micromine нужен для CSV.") };
    form.header = a.header;
  }
  if (given(a, "encoding")) {
    const e = String(a.encoding).trim().toLowerCase();
    const allowed = format === "dat" ? DAT_ENCODINGS : format === "csv" ? CSV_ENCODINGS : [];
    if (!allowed.includes(e)) {
      const hint = allowed.length ? `Допустимо: ${allowed.join(", ")}.` : "Кодировка выбирается для DAT и CSV.";
      return { ok: false, issue: issue("encoding", `Кодировка «${String(a.encoding)}» не подходит формату ${formatInfo(format).title}.`, hint) };
    }
    form.encoding = e;
  }
  if (given(a, "delimiter")) {
    const d = DELIMITER_WORDS[String(a.delimiter).toLowerCase()] ?? DELIMITER_WORDS[String(a.delimiter)];
    if (!d) return { ok: false, issue: issue("delimiter", `Разделитель «${String(a.delimiter)}» не подходит.`, `Допустимо: ${DELIMITERS.map((x) => x.title).join(", ")}.`) };
    form.delimiter = d;
  }
  if (given(a, "decimal")) {
    if (a.decimal !== "," && a.decimal !== ".") return { ok: false, issue: issue("decimal", "Десятичный знак — запятая или точка.", 'Укажите decimal: "," или ".".') };
    form.decimal = a.decimal as Decimal;
  }
  if (given(a, "empty")) {
    const text = String(a.empty);
    if (parseEmptyCode(text) === null) return { ok: false, issue: issue("empty", `«${text.trim()}» — не число.`, "Код пустого — число, например -99; без empty пропуск пуст.") };
    Object.assign(form, { emptyOn: true, empty: text });
  }
  return { ok: true, form };
}

/** The body of the arguments (and the model): the form of the arguments checked as the window checks its own. */
export function bodyOfArgs(a: Args, fieldNames: readonly string[] | null = null): { ok: true; modelId: number; form: ExportForm; body: ExportBody } | { ok: false; issue: FormIssue } {
  const got = formOfArgs(a);
  if (!got.ok) return got;
  const made = bodyOfForm(got.form, fieldNames);
  if (!made.ok) return made;
  if (a.overwrite === true) made.body.overwrite = true;
  return { ok: true, modelId: got.form.modelId as number, form: got.form, body: made.body };
}

/** The arguments of the command for the form of the window (the path is the file the user chose). */
export function argsOfForm(f: ExportForm, path: string): Args {
  const args: Args = { model_id: f.modelId, format: f.format, path };
  if (!f.allFields) args.fields = f.fields.join(", ");
  if (f.filterId !== null) args.filter_id = f.filterId;
  if (f.restrictOn) {
    const c = checkDraft(f.restrict);
    if (c.ok) args.restrict = JSON.stringify(c.rule);
  }
  if (f.format === "csv") {
    args.header = f.header;
    args.delimiter = f.delimiter;
    args.decimal = f.decimal;
  }
  if (f.encoding) args.encoding = f.encoding;
  if ((f.format === "dat" || f.format === "csv") && f.emptyOn) {
    const code = parseEmptyCode(f.empty);
    if (code !== null) args.empty = code;
  }
  return args;
}
