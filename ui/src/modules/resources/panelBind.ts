/**
 * The handlers of `resources.open` (the report in the bottom tab «Ресурсы») and `resources.rename`, and what keeps the panel in step with the project (ТЗ 8.10; ADR 0027
 * решение 29): a run of the window that ends shows its result in the panel, a report changed or deleted in the core is read again or leaves the panel, another project
 * clears it. `resources.open` with a `report_id` shows that saved report (a double click on its row in the tree), without — the result of the window. No React.
 */
import type { App } from "../../shell/app";
import { refusal, needProject, needWritable, noted } from "../../shell/objectRun";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { refusalOf, renameReport } from "./api";
import { summaryText } from "./format";
import { reportIdOf, selectedReport } from "./otherCommands";
import { openReport, panelStore, reportChanged, resetPanel, showRun, showTab } from "./panelState";
import { loadReports, reportById, resourcesStore } from "./state";

type Args = Record<string, unknown>;

/** `resources.open {report_id?, template?, cutoff?}`. */
export async function runOpen(app: App, a: Args): Promise<string> {
  const result = resourcesStore.get().run.result;
  const asked = a.template !== undefined || a.cutoff !== undefined;
  // the saved result of the window with a template or a cut-off of another choice is the saved report: the core makes the form again
  const id = typeof a.report_id === "number" ? reportIdOf(app, a) : result?.saved && result.report_id !== null && asked ? result.report_id : null;
  if (id === null) {
    if (result) {
      showRun();
      showTab(app);
      return summaryText(result);
    }
    const sel = selectedReport(app);
    if (sel === null) throw refusal("no_result", "Нет отчёта для показа.", "Постройте отчёт в окне «Отчёт по ресурсам» («Предпросмотр») или укажите report_id.");
    return runOpen(app, { ...a, report_id: sel });
  }
  showTab(app);
  const report = await openReport(id, { ...(typeof a.template === "string" && a.template ? { template: a.template } : {}), ...(a.cutoff !== undefined ? { cutoff: a.cutoff as number | null } : {}) });
  if (!report) throw refusal("report_not_shown", panelStore.get().error ?? "Отчёт не прочитан.", "Обновите раздел «Отчёты по ресурсам» дерева и повторите.");
  return summaryText(report);
}

/** `resources.rename {report_id?, name}`. */
export async function runRename(app: App, a: Args): Promise<string> {
  const id = reportIdOf(app, a);
  const name = String(a.name ?? "").trim();
  if (!name) throw refusal("bad_param", "Не задано новое название отчёта.", "Введите непустое название.");
  if (!reportById(id)) await loadReports();
  const row = reportById(id);
  if (!row) throw refusal("resource_report_not_found", `Отчёт по ресурсам №${id} не найден.`, "Номера отчётов — в разделе «Отчёты» → «Отчёты по ресурсам» дерева.");
  if (row.name === name) return `Отчёт «${name}» уже так называется`;
  try {
    await renameReport(id, name, row.version);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  await loadReports();
  reportChanged(id, false);
  return `Отчёт по ресурсам «${row.name}» переименован в «${name}»`;
}

/** `resources.changed {kind: "resource_report", id, deleted?}`: the report the panel shows is read again, or leaves the panel when it is deleted. */
export function onResourcesEvent(event: string, data: string): void {
  if (event !== "resources.changed") return;
  try {
    const d = JSON.parse(data) as { kind?: unknown; id?: unknown; deleted?: unknown };
    if (d.kind === "resource_report" && typeof d.id === "number") reportChanged(d.id, d.deleted === true);
  } catch {
    /* an event that is not ours */
  }
}

/** The handlers and the subscriptions; returns the function that takes them off. */
export function bindPanel(app: App): () => void {
  app.registry.bind("resources.open", noted(app, (a) => runOpen(app, a)), needProject);
  app.registry.bind("resources.rename", noted(app, (a) => runRename(app, a)), needWritable(app));
  let uid: string | null = projectStore.get().current?.uid ?? null;
  let ended = resourcesStore.get().run.seq;
  const offs = [
    // a run that ends: the panel shows its result (the opened report stays in the tree)
    resourcesStore.subscribe(() => {
      const run = resourcesStore.get().run;
      if (run.phase === "done" && run.result && run.seq !== ended) {
        ended = run.seq;
        showRun();
      }
    }),
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      resetPanel();
    }),
    onCoreEvent(onResourcesEvent),
  ];
  return () => offs.forEach((off) => off());
}
