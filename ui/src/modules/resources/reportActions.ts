/**
 * What can be done with a saved report of the resources (the menu of its row in the tree and the buttons of its properties; ТЗ 8.10; ADR 0027 решения 14, 16, 29): show it in
 * the panel, save XLSX or CSV, rebuild it («Перестроить» — `lineage.rebuild`, the same parameters, the object is written over, the version grows), rename, delete (after
 * the question «Влияние изменения» — the comparisons built from it). Every item is a command of the registry; one that cannot run says why (the registry's words, or the
 * state of the report). Pure of React.
 */
import type { App } from "../../shell/app";
import type { MenuItem } from "../../shell/ContextMenu";
import { askText } from "../../shell/dialogs";
import type { Selected } from "../../shell/selection";
import type { ReportListRow } from "./answers";
import { saveReportFile, type ExportFormat, type ExportWhat } from "./exportFile";

export const NODE_KIND = "object:resource_report";

/** The kind of the tree for `selected` (the shell's `ObjectKind` gets `resource_report` — by the orchestrator). */
export const SELECT_KIND = "resource_report";

export type Run = (id: string, args: Record<string, unknown>) => void;

/** Why a report cannot be rebuilt now; null — it can. */
export function rebuildReason(r: Pick<ReportListRow, "state" | "graph">): string | null {
  if (r.graph.source_lost) return "Источник отчёта удалён (модель или поле): перестроить нельзя, таблица отчёта остаётся";
  if (r.state === "building") return "Отчёт строится";
  return null;
}

/** Why a report cannot be exported or shown now; null — it can. */
export const readyReason = (r: Pick<ReportListRow, "state">): string | null => (r.state === "building" ? "Отчёт строится" : r.state === "error" ? "Отчёт не построен: перестройте его" : null);

/** The title of the state of a report in the tree: «строится», «ошибка». «Устарел» is the badge of the graph (Д-35), after the name. */
export const stateTitle = (state: ReportListRow["state"]): string => (state === "building" ? "строится" : state === "error" ? "ошибка" : "");

export const reportLabel = (r: Pick<ReportListRow, "name" | "state">): string => (r.state === "ready" ? r.name : `${r.name} · ${stateTitle(r.state)}`);

/** The reports in the order of the tree: by name, the number breaks a tie. */
export const reportsByName = <T extends Pick<ReportListRow, "id" | "name">>(rows: readonly T[]): T[] => [...rows].sort((a, b) => a.name.localeCompare(b.name, "ru") || a.id - b.id);

/** The report is chosen: its «Свойства» show it. */
export function selectReport(app: App, id: number): void {
  app.store.set({ selected: { kind: SELECT_KIND, id } as unknown as Selected });
  const w = app.store.get().workspace;
  if (!(w.right && w.rightTab === "properties")) void app.run("panel.set", { panel: "properties", open: true });
}

/** Asks the new name in our own window and renames the report. */
export async function askRename(r: Pick<ReportListRow, "id" | "name">, run: Run): Promise<void> {
  const name = await askText({ title: "Переименовать отчёт по ресурсам", label: "Название", initial: r.name, okLabel: "Переименовать" });
  if (name !== null && name.trim() && name.trim() !== r.name) run("resources.rename", { report_id: r.id, name: name.trim() });
}

/** Saves the report in a file chosen in the window of the system; the answer goes to the status bar. */
export async function saveFromRow(app: App, r: Pick<ReportListRow, "id" | "name">, format: ExportFormat, what: ExportWhat = "table"): Promise<void> {
  const note = await saveReportFile(app, { id: r.id, name: r.name, format, what });
  if (note) app.store.set({ statusNote: note.text });
}

/** The items of the menu of a report (the tree) and of its properties. */
export function reportActions(app: App, r: ReportListRow): MenuItem[] {
  const reason = (id: string) => app.registry.reason(id);
  const run: Run = (id, args) => void app.run(id, args);
  const ready = readyReason(r);
  return [
    { label: "Показать в панели «Ресурсы»", reason: reason("resources.open") ?? ready, run: () => run("resources.open", { report_id: r.id }) },
    { label: "Сохранить XLSX…", reason: reason("resources.export") ?? ready, run: () => void saveFromRow(app, r, "xlsx") },
    { label: "Сохранить CSV таблицы…", reason: reason("resources.export") ?? ready, run: () => void saveFromRow(app, r, "csv") },
    { label: "PDF", reason: "Появится на этапе Э22" },
    { label: "Перестроить", reason: reason("lineage.rebuild") ?? rebuildReason(r), run: () => run("lineage.rebuild", { kind: NODE_KIND, id: r.id }) },
    { label: "Переименовать…", reason: reason("resources.rename") ?? (r.state === "building" ? "Отчёт строится" : null), run: () => void askRename(r, run) },
    { label: "Удалить…", reason: reason("resources.delete") ?? (r.state === "building" ? "Отчёт строится" : null), run: () => run("resources.delete", { report_id: r.id }) },
  ];
}
