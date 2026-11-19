/**
 * «Экспорт проекта › Объекты в файлы» (Д-17; ADR 0010 §1, ADR 0017 §5): every object of the chosen types into the
 * files of the format chosen for its type, in a new folder «‹Проект› — экспорт ГГГГ-ММ-ДД» inside the chosen one.
 * The table «тип → формат» comes from `GET /export/formats` (a type: how many objects, its formats, which of them
 * the core can write now, why not, the default); the job `export.objects` starts with
 * `POST /export/objects {formats, folder, types?, ids?}` and reports the files it wrote and what it skipped.
 * Pure model and the calls of the core; no React (ExportObjects.tsx draws it, bind.ts runs `project.export`).
 */
import { api } from "../../shell/api";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { askConfirm, type ConfirmOptions } from "../../shell/dialogs";
import { refusal, showJobs, viewport } from "../../shell/objectRun";
import { errorText } from "../../shell/props";
import { count, counted } from "../../shell/treeActions";
import { openWindow } from "../../shell/windows";
import { refresh as refreshJobs } from "../jobs/state";
import { projectStore } from "./state";

export interface FormatOption {
  id: string;
  title: string;
  ext: string;
  /** null — the core writes it now; a Russian text — why not («Экспорт блочных моделей в DAT — этап Э17»). */
  reason: string | null;
}

export interface TypeRow {
  kind: string;
  title: string;
  count: number;
  formats: FormatOption[];
  default: string | null;
  /** Why the whole type is not exported (the core said so). */
  reason: string | null;
}

export interface FormatsInfo {
  types: TypeRow[];
  /** Why nothing can be exported (a project open only for reading, ТЗ 5.7). */
  reason: string | null;
}

/** The chosen format of each type and the types checked for the export. */
export interface Choice {
  formats: Record<string, string>;
  types: string[];
}

export interface ObjectsExportBody {
  formats: Record<string, string>;
  folder: string;
  types: string[];
  ids?: number[];
}

/** The types of ADR 0017 §5 in the order of the window, with the folders the export makes for them. */
export const KIND_TITLES: Record<string, string> = {
  drillhole_set: "Наборы скважин",
  wireframe: "Каркасы",
  dtm: "ЦТМ",
  line_file: "Линии",
  cloud: "Облака точек",
  raster: "Растры",
  block_model: "Блочные модели",
};
const KIND_ORDER = Object.keys(KIND_TITLES);

const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const reasonOf = (o: Record<string, unknown>): string | null => {
  const why = str(o.reason) ?? str(o.why);
  const hint = str(o.hint);
  if (why) return hint && !why.includes(hint) ? `${why.replace(/[.\s]+$/, "")}. ${hint}` : why;
  return o.available === false ? "Сейчас недоступно" : null;
};

function parseFormat(raw: unknown): FormatOption | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.id) ?? str(o.format);
  if (!id) return null;
  const ext = str(o.ext) ?? "";
  return { id, title: str(o.title) ?? id.toUpperCase(), ext: ext && !ext.startsWith(".") ? `.${ext}` : ext, reason: reasonOf(o) };
}

/** The answer of `GET /export/formats`, read leniently: unknown fields are ignored, missing ones get defaults. */
export function parseFormats(raw: unknown): FormatsInfo {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const list = Array.isArray(o.types) ? o.types : [];
  const types: TypeRow[] = [];
  for (const t of list) {
    if (!t || typeof t !== "object") continue;
    const r = t as Record<string, unknown>;
    const kind = str(r.kind);
    if (!kind || types.some((x) => x.kind === kind)) continue;
    const formats = (Array.isArray(r.formats) ? r.formats : []).map(parseFormat).filter((f): f is FormatOption => f !== null);
    const def = str(r.default) ?? str(r.default_format);
    const n = typeof r.count === "number" && Number.isFinite(r.count) ? Math.max(0, Math.round(r.count)) : 0;
    types.push({ kind, title: str(r.title) ?? KIND_TITLES[kind] ?? kind, count: n, formats, default: def && formats.some((f) => f.id === def) ? def : null, reason: reasonOf(r) });
  }
  const rank = (k: string) => (KIND_ORDER.includes(k) ? KIND_ORDER.indexOf(k) : KIND_ORDER.length);
  types.sort((a, b) => rank(a.kind) - rank(b.kind));
  const readOnly = o.read_only === true ? "Проект открыт только для чтения: экспорт недоступен." : null;
  return { types, reason: str(o.reason) ?? readOnly };
}

export function loadFormats(): Promise<FormatsInfo> {
  return api.get<unknown>("/export/formats").then(parseFormats);
}

/** Why the type is not exported: the core's reason, no objects, no format the core writes now; null — it is. */
export function typeReason(row: TypeRow): string | null {
  if (row.reason) return row.reason;
  if (row.count === 0) return "В проекте нет объектов этого типа";
  if (!row.formats.some((f) => f.reason === null)) return row.formats.find((f) => f.reason)?.reason ?? "Нет формата для записи";
  return null;
}

/** The format to use: the wanted one if the core writes it, else the default, else the first available. */
export function fitFormat(row: TypeRow, wanted?: string | null): string | null {
  const ok = (id: string | null | undefined) => (id && row.formats.some((f) => f.id === id && f.reason === null) ? id : null);
  return ok(wanted) ?? ok(row.default) ?? row.formats.find((f) => f.reason === null)?.id ?? null;
}

/** The first state of the window: every exportable type checked in its default (or given) format. */
export function initialChoice(info: FormatsInfo, given: { formats?: Record<string, string>; types?: readonly string[] } = {}): Choice {
  const formats: Record<string, string> = {};
  const types: string[] = [];
  for (const row of info.types) {
    const f = fitFormat(row, given.formats?.[row.kind]);
    if (f) formats[row.kind] = f;
    if (!typeReason(row) && (!given.types || given.types.includes(row.kind))) types.push(row.kind);
  }
  return { formats, types };
}

export function toggleType(choice: Choice, kind: string, on: boolean): Choice {
  const types = on ? (choice.types.includes(kind) ? choice.types : [...choice.types, kind]) : choice.types.filter((k) => k !== kind);
  return { ...choice, types };
}

export const setFormat = (choice: Choice, kind: string, format: string): Choice => ({ ...choice, formats: { ...choice.formats, [kind]: format } });

/** Objects the export writes: the counts of the checked types. */
export function chosenObjects(info: FormatsInfo, choice: Choice): number {
  return info.types.filter((t) => choice.types.includes(t.kind)).reduce((n, t) => n + t.count, 0);
}

/** Why «Экспортировать» is off, told before the request; null — ready. */
export function choiceReason(info: FormatsInfo, choice: Choice, folder: string | null): string | null {
  if (info.reason) return info.reason;
  const checked = info.types.filter((t) => choice.types.includes(t.kind));
  if (!checked.length) return "Отметьте хотя бы один тип объектов.";
  for (const t of checked) {
    const why = typeReason(t);
    if (why) return `${t.title}: ${why}.`;
    const f = t.formats.find((x) => x.id === choice.formats[t.kind]);
    if (!f) return `${t.title}: выберите формат.`;
    if (f.reason) return `${t.title}, ${f.title}: ${f.reason}.`;
  }
  if (!folder) return "Выберите папку: «Выбрать папку…».";
  return null;
}

export function exportBody(info: FormatsInfo, choice: Choice, folder: string, ids?: readonly number[]): ObjectsExportBody {
  const types = info.types.filter((t) => choice.types.includes(t.kind)).map((t) => t.kind);
  const body: ObjectsExportBody = { formats: Object.fromEntries(types.map((k) => [k, choice.formats[k]])), folder, types };
  if (ids?.length) body.ids = [...ids];
  return body;
}

/** Starts the job `export.objects`: its source and number (seen in «Задания»). */
export function startObjectsExport(body: ObjectsExportBody): Promise<{ source: string; id: number }> {
  return api.post("/export/objects", body);
}

/** The folder the export makes inside the chosen one (the core adds « (2)» when it is there already). */
export function exportFolderName(project: string, date = new Date()): string {
  const d = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  return `${project} — экспорт ${d}`;
}

const lower = (s: string) => s.trim().toLocaleLowerCase("ru-RU");

/** A type named by its id or its title («каркасы», «ЦТМ»); null — unknown. */
export function findKind(info: FormatsInfo, name: string): string | null {
  const n = lower(name);
  const row = info.types.find((t) => lower(t.kind) === n || lower(t.title) === n) ?? null;
  if (row) return row.kind;
  const known = Object.entries(KIND_TITLES).find(([k, t]) => lower(k) === n || lower(t) === n);
  return known ? known[0] : null;
}

/** `formats` of the command: JSON `{"wireframe": "dxf"}` or «wireframe=dxf, line_file=str» (a type by its title too). */
export function parseFormatsArg(info: FormatsInfo, value: unknown): Record<string, string> {
  if (value === undefined || value === null || value === "") return {};
  let pairs: [string, string][];
  const text = String(value).trim();
  if (text.startsWith("{")) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw refusal("bad_formats", "Параметр formats не разобран.", "Пример: wireframe=dxf, line_file=str или JSON {\"wireframe\": \"dxf\"}.");
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw refusal("bad_formats", "Параметр formats — не объект.", "Пример: {\"wireframe\": \"dxf\"}.");
    pairs = Object.entries(parsed as Record<string, unknown>).map(([k, v]) => [k, String(v)]);
  } else {
    pairs = text.split(/[,;\n]+/).filter((p) => p.trim()).map((p) => {
      const m = /^\s*([^=:]+?)\s*[=:]\s*(\S+)\s*$/.exec(p);
      if (!m) throw refusal("bad_formats", `Не разобрано «${p.trim()}» в formats.`, "Пример: wireframe=dxf, line_file=str.");
      return [m[1], m[2]];
    });
  }
  const out: Record<string, string> = {};
  for (const [name, fmt] of pairs) {
    const kind = findKind(info, name);
    const row = kind ? info.types.find((t) => t.kind === kind) : undefined;
    if (!kind || !row) throw refusal("export_kind_unknown", `Тип объектов «${name}» не экспортируется.`, `Типы: ${info.types.map((t) => t.kind).join(", ")}.`);
    const f = row.formats.find((x) => lower(x.id) === lower(fmt) || lower(x.title) === lower(fmt));
    if (!f) throw refusal("export_format_unknown", `${row.title}: формата «${fmt}» нет.`, `Форматы: ${row.formats.map((x) => x.id).join(", ")}.`);
    if (f.reason) throw refusal("export_format_unavailable", `${row.title}, ${f.title}: ${f.reason}.`, `Доступные форматы: ${row.formats.filter((x) => !x.reason).map((x) => x.id).join(", ") || "нет"}.`);
    out[kind] = f.id;
  }
  return out;
}

/** `types` of the command: ids or titles by commas; each must be exportable now. */
export function parseTypesArg(info: FormatsInfo, value: unknown): string[] | undefined {
  if (value === undefined || value === null || String(value).trim() === "") return undefined;
  const out: string[] = [];
  for (const name of String(value).split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean)) {
    const kind = findKind(info, name);
    const row = kind ? info.types.find((t) => t.kind === kind) : undefined;
    if (!kind || !row) throw refusal("export_kind_unknown", `Тип объектов «${name}» не экспортируется.`, `Типы: ${info.types.map((t) => t.kind).join(", ")}.`);
    const why = typeReason(row);
    if (why) throw refusal("export_type_unavailable", `${row.title}: ${why}.`, "Уберите этот тип из types.");
    if (!out.includes(kind)) out.push(kind);
  }
  return out;
}

/** Our own question before a call of Claude writes files (ТЗ 7.0.11: never window.confirm). */
export function confirmQuestion(info: FormatsInfo, body: ObjectsExportBody, project: string): ConfirmOptions {
  const lines = info.types
    .filter((t) => body.types.includes(t.kind))
    .map((t) => `• ${t.title} (${count(t.count)}) → ${t.formats.find((f) => f.id === body.formats[t.kind])?.title ?? body.formats[t.kind]}`);
  return {
    title: "Экспорт объектов в файлы",
    message: `Claude просит выгрузить объекты проекта в новую папку «${exportFolderName(project)}» в «${body.folder}»:\n${lines.join("\n")}\nСуществующие файлы не изменяются. Разрешить?`,
    okLabel: "Экспортировать",
  };
}

// ——— the command `project.export {formats, folder, types?}` ———

/** `project.export` with any of formats, folder, types exports the objects; without them it is the .bfggis window. */
export const wantsObjects = (a: Record<string, unknown>): boolean => ["formats", "folder", "types"].some((k) => typeof a[k] === "string" && a[k] !== "");

/**
 * The objects of the project into files: with `folder` the job starts at once (a call of Claude after our own
 * question), without it the window «Объекты в файлы» opens with the choice. Failures are Russian texts with a hint.
 */
export async function exportObjectsCommand(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  try {
    const info = await loadFormats();
    if (info.reason) throw refusal("read_only", info.reason, "Откройте проект для записи.");
    if (app.store.get().projectReadOnly) throw refusal("read_only", "Проект открыт только для чтения: экспорт недоступен.", "Откройте проект для записи.");
    const formats = parseFormatsArg(info, a.formats);
    const types = parseTypesArg(info, a.types);
    const choice = initialChoice(info, { formats, types });
    const folder = typeof a.folder === "string" && a.folder.trim() ? a.folder.trim() : null;
    if (!folder) {
      openWindow("project-export", "Экспорт проекта", "form", { mode: "objects", formats: choice.formats, types: choice.types }, ...viewport());
      return "Открыто окно «Экспорт проекта» (объекты в файлы): выберите папку и нажмите «Экспортировать»";
    }
    const reason = choiceReason(info, choice, folder);
    if (reason) throw refusal("export_nothing", reason, "Проверьте types и formats: типы без объектов не экспортируются.");
    const body = exportBody(info, choice, folder);
    const project = projectStore.get().current?.name ?? app.store.get().projectName ?? "Проект";
    if (source === "claude" && !(await askConfirm(confirmQuestion(info, body, project)))) return "Отменено пользователем";
    const job = await startObjectsExport(body);
    showJobs(app);
    await refreshJobs();
    const what = info.types.filter((t) => body.types.includes(t.kind)).map((t) => `${t.title} → ${body.formats[t.kind]}`).join(", ");
    return `Запущено задание №${job.id} «export.objects»: ${what}; папка «${exportFolderName(project)}» в «${folder}». Ход — на вкладке «Задания»`;
  } catch (e) {
    const text = errorText(e);
    if (source === "user") app.store.set({ statusNote: text });
    throw new Error(text);
  }
}

// ——— the report of a finished job ———

export interface ReportFile {
  path: string;
  size: number | null;
  object: string;
  format: string;
}

export interface ExportReport {
  folder: string | null;
  files: ReportFile[];
  skipped: { object: string; reason: string }[];
  bytes: number;
}

/** The result of `export.objects`: the folder made, its files, what was skipped and why. */
export function reportOf(result: Record<string, unknown> | null | undefined): ExportReport {
  const r = result ?? {};
  const files: ReportFile[] = (Array.isArray(r.files) ? r.files : []).flatMap((f) => {
    if (typeof f === "string") return [{ path: f, size: null, object: "", format: "" }];
    if (!f || typeof f !== "object") return [];
    const o = f as Record<string, unknown>;
    const path = str(o.path);
    if (!path) return [];
    const size = typeof o.size === "number" && Number.isFinite(o.size) ? o.size : null;
    return [{ path, size, object: str(o.object) ?? str(o.name) ?? "", format: str(o.format) ?? "" }];
  });
  const skipped = (Array.isArray(r.skipped) ? r.skipped : []).flatMap((s) => {
    if (!s || typeof s !== "object") return [];
    const o = s as Record<string, unknown>;
    return [{ object: str(o.object) ?? str(o.name) ?? "Объект", reason: str(o.reason) ?? "причина не указана" }];
  });
  return { folder: str(r.folder) ?? str(r.path), files, skipped, bytes: files.reduce((n, f) => n + (f.size ?? 0), 0) };
}

/** «1,5 МБ», «820 КБ», «12 Б». */
export function sizeText(bytes: number): string {
  const one = (v: number) => (v >= 10 ? String(Math.round(v)) : v.toFixed(1).replace(".", ","));
  if (bytes < 1024) return `${count(bytes)} Б`;
  if (bytes < 1024 ** 2) return `${one(bytes / 1024)} КБ`;
  if (bytes < 1024 ** 3) return `${one(bytes / 1024 ** 2)} МБ`;
  return `${one(bytes / 1024 ** 3)} ГБ`;
}

/** «Записано 12 файлов (3,4 МБ); пропущено 2» — the head of the report. */
export function reportSummary(report: ExportReport): string {
  const head = report.files.length ? `Записано ${counted(report.files.length, "файл", "файла", "файлов")} (${sizeText(report.bytes)})` : "Файлы не записаны";
  return report.skipped.length ? `${head}; пропущено ${counted(report.skipped.length, "объект", "объекта", "объектов")}` : head;
}

/** A file of the report by its path inside the folder made by the export. */
export function relativePath(path: string, folder: string | null): string {
  if (!folder) return path;
  const f = folder.replace(/[\\/]+$/, "");
  return path.startsWith(f) && /[\\/]/.test(path.charAt(f.length)) ? path.slice(f.length + 1) : path;
}
