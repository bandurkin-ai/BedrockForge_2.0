/**
 * The handler of `blocks.report` and the window «Содержание–тоннаж» of the shell (ТЗ 7.31; ADR 0023 §10). Without
 * `model_id` the command opens the window (the model chosen in the tree, else the first one); with it — checks the
 * arguments (Russian, with a hint and the place in the form; nothing wrong reaches the core), starts the job
 * `blocks.report`, follows it (the stages, «Отменить») and answers with the text of the report; the window shows the
 * same run (the table, the curve, the CSV). The window does everything through the registry (the log, the notice, the
 * question of the assistant). «Копировать CSV» / «Сохранить CSV…» work on the CSV the core made.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../../shell/app";
import type { CommandSource } from "../../../shell/commands";
import { needProject, noted, refusal, viewport } from "../../../shell/objectRun";
import { focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../../shell/windows";
import { cancelJob } from "../../jobs/state";
import { projectStore } from "../../projects/state";
import { blockById, blocksStore, loadBlockDetails } from "../state";
import { loadReportJob, refusalOf, startReport, waitReport, type Refusal } from "./api";
import { summaryText } from "./format";
import { ReportWindow } from "./ReportWindow";
import { beginRun, bodyOfArgs, currentForm, currentRun, fieldsOf, formForModel, formOfArgs, isRunning, issueOfRefusal, patchRun, REPORT_WINDOW, reportStore, resetReport, setForm, setIssue, type Args } from "./state";

export const reportTitle = (model: string | null): string => (model ? `Содержание–тоннаж · ${model}` : "Содержание–тоннаж");

/** The model with its details when they are read (labels of categories, statistics), else from the list. */
export function modelFields(id: number | null) {
  if (id === null) return [];
  return fieldsOf(blocksStore.get().details[id] ?? blockById(id));
}

/** The window «Содержание–тоннаж», if open. */
export const windowOfReport = (): string | null => windowStore.get().windows.find((w) => w.kind === REPORT_WINDOW)?.id ?? null;

/** The model of the window: the one selected in the tree, else the one the form has, else the first of the project. */
export function defaultModel(app: App): number | null {
  const sel = app.store.get().selected;
  if (sel?.kind === "blocks" && blockById(sel.id)) return sel.id;
  const have = currentForm().modelId;
  if (have !== null && blockById(have)) return have;
  return blocksStore.get().list[0]?.id ?? null;
}

/** A model chosen in the window: its defaults of 7.31 (the grades `*_CUT`), the details of its fields are read for the labels. */
export function chooseModel(id: number | null): void {
  const f = formForModel(id, modelFields(id), currentForm());
  reportStore.set({ form: f, issue: null });
  const win = windowOfReport();
  if (win) updateWindow(win, { title: reportTitle(id === null ? null : (blockById(id)?.name ?? null)) });
  if (id !== null)
    void loadBlockDetails(id).then(() => {
      if (currentForm().modelId === id) reportStore.set({ form: formForModel(id, modelFields(id), currentForm()) });
    });
}

export function openReportWindow(app: App): string {
  const open = windowOfReport();
  if (open) {
    focusWindow(open);
    return open;
  }
  if (currentForm().modelId === null || !blockById(currentForm().modelId as number)) chooseModel(defaultModel(app));
  const name = currentForm().modelId === null ? null : (blockById(currentForm().modelId as number)?.name ?? null);
  return openWindow(REPORT_WINDOW, reportTitle(name), "normal", {}, ...viewport());
}

const notFound = (id: number) => refusal("block_model_not_found", `Блочная модель №${id} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");

const asError = (r: Refusal) => refusal(r.code, r.message, r.hint);

/** The run of `blocks.report` with the arguments: the job followed to its end; the answer — the text of the report. */
export async function runReport(app: App, a: Args, source: CommandSource): Promise<string> {
  if (a.model_id === undefined || a.model_id === null) {
    openReportWindow(app);
    return "Открыто окно «Содержание–тоннаж»";
  }
  const got = bodyOfArgs(a);
  if (!got.ok) {
    setIssue(got.issue);
    throw refusal("bad_param", got.issue.message, got.issue.hint);
  }
  const model = blockById(got.modelId);
  if (!model) throw notFound(got.modelId);
  if (isRunning()) throw refusal("report_running", "Отчёт уже строится.", "Дождитесь конца или отмените его в окне «Содержание–тоннаж».");
  // the window shows what is asked (the form of the call), and the run
  reportStore.set({ form: formOfArgs(a, modelFields(got.modelId), currentForm()) });
  if (source === "user" || windowOfReport()) openReportWindow(app);
  const win = windowOfReport();
  if (win) updateWindow(win, { title: reportTitle(model.name) });
  const seq = beginRun(got.modelId, model.name);
  const fail = (r: Refusal): never => {
    if (patchRun(seq, { phase: "failed", error: r })) setIssue(issueOfRefusal(r, currentForm())); // a stale run does not mark the form of the new one
    throw asError(r);
  };
  const uid = projectStore.get().current?.uid ?? null;
  let started;
  try {
    started = await startReport(got.modelId, got.body);
  } catch (e) {
    return fail(refusalOf(e));
  }
  patchRun(seq, { job: started });
  let end;
  try {
    end = await waitReport(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (currentRun().seq !== seq || (projectStore.get().current?.uid ?? null) !== uid) return "Проект изменился: отчёт больше не показывается";
  if (end.status === "cancelled") {
    patchRun(seq, { phase: "cancelled" });
    return `Отчёт по модели «${model.name}» отменён: ничего не записано`;
  }
  if (end.status === "failed") {
    const e = end.error;
    return fail({ code: e?.code ?? "job_failed", message: e?.message ?? "Отчёт не построен.", hint: e?.hint ?? "Повторите отчёт.", param: null });
  }
  let full;
  try {
    full = await loadReportJob(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (!full.result) return fail({ code: "bad_answer", message: "Ядро вернуло отчёт без таблицы.", hint: "Повторите отчёт; если повторится — обновите программу.", param: null });
  patchRun(seq, { phase: "done", result: full.result });
  return summaryText(full.result, model.name);
}

/** «Отменить» of the window: the job of the run is cancelled between the portions; nothing is written (the report writes nothing). */
export async function cancelReport(): Promise<void> {
  const job = currentRun().job;
  if (job && isRunning()) await cancelJob(job);
}

export function bindReport(app: App): void {
  app.registry.bind(
    "blocks.report",
    noted(app, (a, source) => runReport(app, a, source)),
    (ctx) => needProject(ctx) ?? (blocksStore.get().loaded && !blocksStore.get().list.length ? "В проекте нет блочных моделей" : null),
  );
  // the report belongs to the project: another project — a clean window
  let uid: string | null = projectStore.get().current?.uid ?? null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetReport();
  });
  // a model that is gone leaves the form (the result of it stays until the next run)
  blocksStore.subscribe(() => {
    const id = currentForm().modelId;
    if (id !== null && blocksStore.get().loaded && !blockById(id)) setForm({ modelId: null, grades: [] });
  });
}

/** The window for the shell's window layer (`shell/Shell.tsx`); null — not this window. */
export function renderReportWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== REPORT_WINDOW) return null;
  return createElement(ReportWindow, { winId: w.id });
}
