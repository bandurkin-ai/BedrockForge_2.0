/**
 * The handler of `resources.report` and the window «Отчёт по ресурсам» of the shell (ТЗ 8.10; ADR 0027 решения 16, 29). Without `model_id` the
 * command opens the window (the model chosen in the tree, else the one the form has, else the first); with it — turns the arguments into
 * the request (every wrong thing is told in Russian with a hint and the place in the form; nothing wrong reaches the core), starts the
 * job `resources.report` (`save: false` — the preview, nothing is written, it works in a project open for reading), follows it (the stages,
 * «Отменить») and answers with the text of the result; the window shows the same run. The window does everything through the registry
 * (the log, the notice, the question of the assistant). The other commands (`resources.curve`, `.export`, `.delete`, `.drill`, `.compare`) —
 * `otherCommands.ts`; `resources.templates` waits for its window (20.14). One app at a time (the tests create several): `bindResources`
 * answers the function that unbinds the listeners.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { needProject, noted, refusal, viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../shell/windows";
import { blockById, blocksStore, loadBlockDetails } from "../blocks/state";
import { cancelJob, onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { loadReportJob, refusalOf, startReport, waitJobEnd, type Refusal } from "./api";
import { argsOfForm, formOfArgs, issueOfRefusal, paramsOfForm, type Args } from "./form";
import { summaryText } from "./format";
import { bindOtherCommands } from "./otherCommands";
import { ReportWindow } from "./ReportWindow";
import { beginRun, currentForm, currentRun, ensureDefaults, formForModel, isRunning, loadReports, loadTemplates, modelFields, patchRun, resetResources, resourcesEvent, resourcesStore, RESOURCES_WINDOW, setForm, setIssue, TITLE } from "./state";

export const reportTitle = (model: string | null): string => (model ? `${TITLE} · ${model}` : TITLE);

/** The window «Отчёт по ресурсам», if open. */
export const windowOfReport = (): string | null => windowStore.get().windows.find((w) => w.kind === RESOURCES_WINDOW)?.id ?? null;

/** The model of the window: the one selected in the tree, else the one the form has, else the first of the project. */
export function defaultModel(app: App): number | null {
  const sel = app.store.get().selected;
  if (sel?.kind === "blocks" && blockById(sel.id)) return sel.id;
  const have = currentForm().modelId;
  if (have !== null && blockById(have)) return have;
  return blocksStore.get().list[0]?.id ?? null;
}

/** A model chosen in the window: what depends on its fields is cleared, the details of its fields (labels, statistics) are read. */
export function chooseModel(id: number | null): void {
  resourcesStore.set({ form: formForModel(id, modelFields(id), currentForm()), issue: null });
  const win = windowOfReport();
  if (win) updateWindow(win, { title: reportTitle(id === null ? null : (blockById(id)?.name ?? null)) });
  if (id !== null)
    void loadBlockDetails(id).then(() => {
      if (currentForm().modelId === id) resourcesStore.set({ form: formForModel(id, modelFields(id), currentForm()) });
    });
}

export function openReportWindow(app: App): string {
  void ensureDefaults();
  void loadTemplates();
  const open = windowOfReport();
  if (open) {
    focusWindow(open);
    return open;
  }
  const id = currentForm().modelId;
  if (id === null || !blockById(id)) chooseModel(defaultModel(app));
  const cur = currentForm().modelId;
  return openWindow(RESOURCES_WINDOW, reportTitle(cur === null ? null : (blockById(cur)?.name ?? null)), "normal", {}, ...viewport());
}

const notFound = (id: number) => refusal("block_model_not_found", `Блочная модель №${id} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");

const asError = (r: Refusal) => refusal(r.code, r.message, r.hint);

/** The same arguments in a fixed order: the window runs the command with the arguments of its form, and keeps its form when it is the same. */
const same = (a: Args, b: Args): boolean => JSON.stringify(Object.entries(a).sort(([x], [y]) => (x < y ? -1 : 1))) === JSON.stringify(Object.entries(b).sort(([x], [y]) => (x < y ? -1 : 1)));

/** The run of `resources.report` with the arguments: the job followed to its end; the answer — the text of the result. */
export async function runReport(app: App, a: Args, source: CommandSource): Promise<string> {
  if (a.model_id === undefined || a.model_id === null) {
    openReportWindow(app);
    return `Открыто окно «${TITLE}»`;
  }
  const got = formOfArgs(a, currentForm());
  if (!got.ok) {
    setIssue(got.issue);
    throw refusal("bad_param", got.issue.message, got.issue.hint);
  }
  const built = paramsOfForm(got.form, resourcesStore.get().defaults?.limits);
  if (!built.ok) {
    setIssue(built.issue);
    throw refusal("bad_param", built.issue.message, built.issue.hint);
  }
  const model = blockById(got.form.modelId as number);
  if (!model) throw notFound(got.form.modelId as number);
  const save = a.save === true;
  if (save && app.store.get().projectReadOnly) throw refusal("project_read_only", "Проект открыт только для чтения: отчёт не записан.", "Предпросмотр работает и так; чтобы сохранить отчёт, откройте проект для записи.");
  if (isRunning()) throw refusal("report_running", "Отчёт уже строится.", `Дождитесь конца или отмените его в окне «${TITLE}».`);
  // the window shows what is asked (the form of the call); the form of the window itself stays as it is when it made the call
  const mine = argsOfForm(currentForm(), save);
  if (!(mine.ok && same(mine.args, a))) resourcesStore.set({ form: got.form, issue: null });
  if (source === "user" || windowOfReport()) openReportWindow(app);
  const win = windowOfReport();
  if (win) updateWindow(win, { title: reportTitle(model.name) });
  const seq = beginRun(model.id, model.name, save);
  const fail = (r: Refusal): never => {
    if (patchRun(seq, { phase: "failed", error: r })) setIssue(issueOfRefusal(r, currentForm())); // a stale run does not mark the form of the new one
    throw asError(r);
  };
  const uid = projectStore.get().current?.uid ?? null;
  let started;
  try {
    started = await startReport({ ...built.built, save });
  } catch (e) {
    return fail(refusalOf(e));
  }
  patchRun(seq, { job: started });
  let end;
  try {
    end = await waitJobEnd(started);
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
  if (full.result.saved) void loadReports();
  return summaryText(full.result);
}

/** «Отменить» of the window: the job of the run is cancelled between the stages; the preview writes nothing, a save writes all or nothing. */
export async function cancelReport(): Promise<void> {
  const job = currentRun().job;
  if (job && isRunning()) await cancelJob(job);
}

export function bindResources(app: App): () => void {
  app.registry.bind(
    "resources.report",
    noted(app, (a, source) => runReport(app, a, source)),
    (ctx) => needProject(ctx) ?? (blocksStore.get().loaded && !blocksStore.get().list.length ? "В проекте нет блочных моделей" : null),
  );
  bindOtherCommands(app);
  // the report belongs to the project: another project — a clean window and the lists read again
  let uid: string | null = projectStore.get().current?.uid ?? null;
  if (uid) void loadReports();
  const offs = [
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      resetResources();
      if (cur) void loadReports();
    }),
    // a model that is gone leaves the form (the result of it stays until the next run)
    blocksStore.subscribe(() => {
      const id = currentForm().modelId;
      if (id !== null && blocksStore.get().loaded && !blockById(id)) setForm({ modelId: null, elements: [], groups: [], cutoffField: "", densityField: "" });
    }),
    onCoreEvent((event, data) => {
      if (event !== "resources.changed" || !projectStore.get().current) return;
      if (resourcesEvent(data) === "template") void loadTemplates();
      else void loadReports();
    }),
  ];
  return () => offs.forEach((off) => off());
}

/** The window for the shell's window layer (`shell/Shell.tsx`); null — not this window. */
export function renderResourcesWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== RESOURCES_WINDOW) return null;
  return createElement(ReportWindow, { winId: w.id });
}
