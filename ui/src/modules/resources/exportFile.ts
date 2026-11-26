/**
 * «Сохранить XLSX…» / «Сохранить CSV…» of the result panel, the tree and the properties of a report (ТЗ 8.10 «Вывод»; ADR 0027 решение 20): the file is chosen in
 * the save window of the application (`bridge().pickSave`, which itself asks before a file is replaced), then the command `resources.export` runs (the log, the
 * status, the assistant see it) and the core writes the file — XLSX with the sheets «Отчёт» (the form), «Таблица», «Кривая», «Контроль», «Параметры», CSV with `;` and
 * a decimal comma (the table, or the form). A file written is never half written (`‹имя›.part` is renamed). Only a saved report is exported: the core keeps
 * nothing of a preview. No React.
 */
import type { App } from "../../shell/app";
import { bridge } from "../../bridge";
import { errorText } from "../../shell/props";

export type ExportFormat = "xlsx" | "csv";
/** CSV only: the table of the rows, or the form of the template (one file each). */
export type ExportWhat = "table" | "form";

export const EXPORT_WHAT_TITLES: Record<ExportWhat, string> = { table: "таблица", form: "форма" };

/** The name of the file offered: «Отчёт по ресурсам.xlsx», «… (форма).csv»; the signs Windows does not allow are replaced. */
export function exportFileName(name: string, format: ExportFormat, what: ExportWhat = "table"): string {
  const base = name.replace(/[\\/:*?"<>|]+/g, "_").replace(/\s+/g, " ").trim() || "Отчёт по ресурсам";
  return `${base}${format === "csv" && what === "form" ? " (форма)" : ""}.${format}`;
}

export interface ExportAsk {
  id: number;
  name: string;
  format: ExportFormat;
  what?: ExportWhat;
  /** the template of the sheet «Отчёт» / of the CSV of the form; "" — the one saved with the report */
  template?: string;
  /** the cut-off of the form; undefined — the first */
  cutoff?: number | null;
}

/** The arguments of `resources.export` for a chosen file: what the core asks (`overwrite` — the window of the system has asked already). */
export function exportArgs(a: ExportAsk, path: string): Record<string, unknown> {
  const args: Record<string, unknown> = { report_id: a.id, format: a.format, path, overwrite: true };
  if (a.format === "csv" && a.what === "form") args.what = "form";
  if (a.template) args.template = a.template;
  if (a.cutoff !== undefined) args.cutoff = a.cutoff;
  return args;
}

export interface ExportNote {
  text: string;
  error: boolean;
}

/** The save window, then the command; null — the person closed the window (nothing is written). */
export async function saveReportFile(app: App, a: ExportAsk): Promise<ExportNote | null> {
  const b = bridge();
  if (!b) return { text: "Сохранение файлов доступно только в приложении.", error: true };
  let path: string | null;
  try {
    path = await b.pickSave(exportFileName(a.name, a.format, a.what), a.format);
  } catch (e) {
    return { text: errorText(e), error: true };
  }
  if (!path) return null;
  const r = await app.registry.execute("resources.export", exportArgs(a, path), "user");
  return { text: r.result, error: !r.ok };
}
