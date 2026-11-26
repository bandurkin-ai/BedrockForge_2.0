/**
 * The handlers of the other commands of the resource report (ТЗ 8.10, Д-36, Д-37; ADR 0027 решение 29; docs/progress/e20-api.md §5, §8–§10):
 * - `resources.curve` — the curve of a report as text (the chart is the result panel's, 20.13);
 * - `resources.export` — `POST /resources/reports/{id}/export` (XLSX or CSV; a file is not replaced unless `overwrite`);
 * - `resources.delete` — after the question «Влияние изменения» (`askImpact`: the comparisons built from the report) the report goes;
 * - `resources.drill` — the rules and the numbers of a cell; opens the window «Переход к блокам» and puts the rules as the temporary filter in the 3D (`drill.ts`, 20.15);
 * - `resources.compare` — two reports row by row.
 * The report of a command is `report_id`, else the one selected in the tree, else the one the window built last. No React.
 */
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { needProject, needWritable, noted, refusal } from "../../shell/objectRun";
import { askImpact } from "../lineage/impactAsk";
import { compareReports, deleteReport, loadExportJob, loadReport, refusalOf, startExport, waitJobEnd, type DrillBody, type ExportBody, type Refusal } from "./api";
import { bindDrill, DRILL_TITLE, drillStore, showCell } from "./drill";
import { compareText, curveText, drillText, fmtCount } from "./format";
import { openCompareWindow, windowOfCompare } from "./compareBind";
import { loadCompare, COMPARE_TITLE } from "./compareState";
import { currentRun, loadReports, reportById, resourcesStore } from "./state";

type Args = Record<string, unknown>;

const asError = (r: Refusal) => refusal(r.code, r.message, r.hint);
const NO_REPORT = (id: number) => refusal("resource_report_not_found", `Отчёт по ресурсам №${id} не найден.`, "Номера отчётов — в разделе «Отчёты» → «Отчёты по ресурсам» дерева.");

/** The report selected in the tree (`kind` is the tree's own: `resource_report`; the tree of 20.13 adds it to `ObjectKind` of the shell), else null. */
export function selectedReport(app: App): number | null {
  const sel = app.store.get().selected as { kind: string; id: number } | null;
  return sel && (sel.kind === "resource_report" || sel.kind === "resources") ? sel.id : null;
}

/** The report of a command: `report_id`, else the one selected in the tree, else the one the window saved last. */
export function reportIdOf(app: App, a: Args): number {
  const known = (id: number) => {
    if (resourcesStore.get().reportsLoaded && !reportById(id)) throw NO_REPORT(id);
    return id;
  };
  if (typeof a.report_id === "number") return known(a.report_id);
  const sel = selectedReport(app);
  if (sel !== null) return known(sel);
  const last = currentRun().result;
  if (last?.saved && last.report_id !== null) return known(last.report_id);
  throw refusal("bad_param", "Не задан отчёт по ресурсам.", "Передайте report_id или выберите отчёт в разделе «Отчёты» → «Отчёты по ресурсам» дерева.");
}

const nameOf = (id: number): string => reportById(id)?.name ?? `№${id}`;

/** `resources.curve {report_id?}`: the curve of the report (a preview of the window without an id). */
export async function runCurve(app: App, a: Args): Promise<string> {
  const run = currentRun().result;
  if (typeof a.report_id !== "number" && selectedReport(app) === null && run?.curve) return curveText(run, run.curve); // the preview of the window
  const id = reportIdOf(app, a);
  try {
    const r = await loadReport(id, { size: 1 });
    if (!r.curve) return `У отчёта «${r.name}» нет кривой: она выключена (0 точек). Постройте отчёт заново с кривой от 2 до 500 точек.`;
    return curveText(r, r.curve);
  } catch (e) {
    throw asError(refusalOf(e));
  }
}

/** `resources.export {report_id?, format, path, template?, cutoff?, overwrite?}`: the job `resources.export` followed to its end. */
export async function runExport(app: App, a: Args): Promise<string> {
  const id = reportIdOf(app, a);
  const body: ExportBody = { format: a.format as "xlsx" | "csv", path: String(a.path).trim() };
  if (!body.path) throw refusal("bad_param", "Не задан путь файла.", "Укажите полный путь, например C:\\Отчёты\\resources.xlsx.");
  if (typeof a.template === "string" && a.template.trim()) body.template = a.template.trim();
  if (a.cutoff !== undefined) body.cutoff = a.cutoff as number | null;
  if (a.what === "form" || a.what === "both") body.what = a.what;
  if (a.overwrite === true) body.overwrite = true;
  try {
    const started = await startExport(id, body);
    const end = await waitJobEnd(started);
    if (end.status === "cancelled") return "Экспорт отменён: файл не записан";
    const done = await loadExportJob(started);
    if (end.status === "failed" || done.error) {
      const e = done.error ?? end.error;
      throw refusal(e?.code ?? "job_failed", e?.message ?? "Файл не записан.", e?.hint ?? "Повторите экспорт.");
    }
    const r = done.result;
    return r ? `Отчёт «${nameOf(id)}» записан в файл ${r.path}${r.rows !== null ? `, строк ${fmtCount(r.rows)}` : ""}${r.bytes !== null ? `, ${fmtCount(r.bytes)} байт` : ""}` : `Отчёт «${nameOf(id)}» записан в файл ${body.path}`;
  } catch (e) {
    throw asError(refusalOf(e)); // a refusal made here is a CoreError too: it keeps its words
  }
}

/** `resources.delete {report_id?}`: the question of the graph (the comparisons built from the report), then the deletion. */
export async function runDelete(app: App, a: Args, source: CommandSource): Promise<string> {
  const id = reportIdOf(app, a);
  const name = nameOf(id);
  if (source === "user" && !(await askConfirm({ title: "Удалить отчёт по ресурсам", message: `Удалить отчёт «${name}»? Таблица, кривая и контроль отчёта удалятся из проекта; файлы XLSX и CSV вне проекта останутся.`, okLabel: "Удалить", danger: true }))) return "Отменено";
  if (!(await askImpact({ kind: "object:resource_report", id }, `удалить отчёт по ресурсам «${name}»`))) return "Отменено";
  try {
    await deleteReport(id);
  } catch (e) {
    throw asError(refusalOf(e));
  }
  void loadReports();
  return `Отчёт «${name}» удалён`;
}

/** The labels of the keys of a cell: «Измеренные; A» → two labels; nothing — the total. */
export const labelsOf = (text: unknown): string[] | null => {
  const l = typeof text === "string" ? text.split(";").map((s) => s.trim()).filter(Boolean) : [];
  return l.length ? l : null;
};

/** `resources.drill {report_id?, group?, cutoff?, kind?}`: the numbers and the rules of a cell; the window «Переход к блокам» opens and the 3D shows the blocks of the cell. */
export async function runDrill(app: App, a: Args): Promise<string> {
  const id = reportIdOf(app, a);
  const body: DrillBody = { group: labelsOf(a.group), cutoff: typeof a.cutoff === "number" ? a.cutoff : null };
  if (typeof a.kind === "string") body.kind = a.kind as DrillBody["kind"];
  const label = body.group ? body.group.join(" / ") : "Итого";
  const answer = await showCell({ reportId: id, reportName: nameOf(id), label, body });
  if (!answer) {
    const e = drillStore.get().error;
    throw asError(e ?? { code: "drill_replaced", message: "Переход к блокам заменён другим.", hint: "Повторите команду.", param: null });
  }
  const notes = drillStore.get().notes;
  const shown = drillStore.get().applied ? "В 3D показаны только блоки ячейки (временный фильтр: «Сбросить» в окне или закрытие окна его снимает)." : "";
  return [drillText(answer, nameOf(id)), `Открыто окно «${DRILL_TITLE}»: таблица блоков ячейки, блок — в инспектор.`, shown, ...notes].filter(Boolean).join("\n");
}

/**
 * `resources.compare {report_a?, report_b?}`: without them the window «Сравнить отчёты» opens; with both from the interface the window opens and shows the
 * comparison; from the assistant — the text (and the window, if it is open, shows the same). Two reports row by row; A = B gives 0 everywhere.
 */
export async function runCompare(a: Args, source: CommandSource): Promise<string> {
  const given = [a.report_a, a.report_b].filter((x) => x !== undefined).length;
  if (given === 0) {
    openCompareWindow();
    return `Открыто окно «${COMPARE_TITLE}»`;
  }
  if (given === 1) throw refusal("bad_param", "Нужны оба отчёта: report_a и report_b.", "Передайте номера двух отчётов или вызовите команду без параметров: откроется окно выбора.");
  const ids: [number, number] = [a.report_a as number, a.report_b as number];
  try {
    if (source === "user" || windowOfCompare()) {
      if (source === "user") openCompareWindow();
      return compareText(await loadCompare(...ids));
    }
    return compareText(await compareReports(...ids));
  } catch (e) {
    throw asError(refusalOf(e));
  }
}

/** Binds the commands; answers the function that unbinds the listeners of the transition (the click on a cell, the project, the window). */
export function bindOtherCommands(app: App): () => void {
  const r = app.registry;
  r.bind("resources.curve", noted(app, (a) => runCurve(app, a)), needProject);
  r.bind("resources.export", noted(app, (a) => runExport(app, a)), needProject);
  r.bind("resources.delete", noted(app, (a, s) => runDelete(app, a, s)), needWritable(app));
  r.bind("resources.drill", noted(app, (a) => runDrill(app, a)), needProject);
  const offDrill = bindDrill(app, nameOf);
  r.bind("resources.compare", noted(app, (a, s) => runCompare(a, s)), needProject);
  return offDrill;
}
