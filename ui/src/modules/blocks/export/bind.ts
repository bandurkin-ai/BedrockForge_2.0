/**
 * The handler of `blocks.export` and the window «Экспорт блочной модели» of the shell (ТЗ 8.7.7, 7.35 Д-17; ADR 0023 §12).
 * Without `model_id` and `path` the command opens the window (the model chosen in the tree, else the first one); with
 * them — checks the arguments (Russian, with a hint and the place in the form; nothing wrong reaches the core), starts the
 * job `blocks.export`, asks «Заменить?» in our own window when the file is there (409 `files_exist`), follows the job
 * (the stage, «Отменить») and answers with the words about the file. The window does everything through the registry
 * (the log, the notice, the question of the assistant). A cancel and a failure leave no file (the core writes «…​.part»
 * and renames it when it is complete).
 */
import { createElement, type ReactNode } from "react";
import { bridge } from "../../../bridge";
import type { App } from "../../../shell/app";
import type { CommandSource } from "../../../shell/commands";
import { askConfirm } from "../../../shell/dialogs";
import { needProject, noted, refusal, viewport } from "../../../shell/objectRun";
import { focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../../shell/windows";
import { cancelJob } from "../../jobs/state";
import { projectStore } from "../../projects/state";
import { overwriteQuestion } from "../../rasters/export";
import { refusalOf, waitReport } from "../report/api";
import { blockById, blocksStore } from "../state";
import { argsOfForm, bodyOfArgs, type Args } from "./args";
import { loadExportJob, startExport } from "./api";
import { ExportWindow } from "./ExportWindow";
import { defaultFileName, formatInfo, issueOfRefusal, resultText, type ExportForm, type Refusal } from "./model";
import { beginRun, currentForm, currentRun, exportStore, EXPORT_WINDOW, isRunning, patchRun, resetExport, setForm, setIssue } from "./state";

export const exportTitle = (model: string | null): string => (model ? `Экспорт блочной модели · ${model}` : "Экспорт блочной модели");

/** The window «Экспорт блочной модели», if open. */
export const windowOfExport = (): string | null => windowStore.get().windows.find((w) => w.kind === EXPORT_WINDOW)?.id ?? null;

/** The names of the fields of a model: from the list of the tree (they are there before the details are read). */
export function fieldNamesOf(id: number | null): string[] | null {
  const m = id === null ? undefined : (blocksStore.get().details[id] ?? blockById(id));
  return m ? m.fields.map((f) => f.name) : null;
}

/** A model with sub-blocks (the core tells it in the list since the model v2). */
export const hasSubBlocks = (id: number | null): boolean => (id === null ? false : ((blockById(id) as { sub?: boolean } | undefined)?.sub ?? false));

/** The model of the window: the one selected in the tree, else the one the form has, else the first of the project. */
export function defaultModel(app: App): number | null {
  const sel = app.store.get().selected;
  if (sel?.kind === "blocks" && blockById(sel.id)) return sel.id;
  const have = currentForm().modelId;
  if (have !== null && blockById(have)) return have;
  return blocksStore.get().list[0]?.id ?? null;
}

/** A model chosen in the window: its fields are all chosen again, the file is chosen again for the new name. */
export function chooseModel(id: number | null): void {
  setForm({ modelId: id, allFields: true, fields: [], path: null, pathFormat: null });
  const win = windowOfExport();
  if (win) updateWindow(win, { title: exportTitle(id === null ? null : (blockById(id)?.name ?? null)) });
}

export function openExportWindow(app: App): string {
  const open = windowOfExport();
  if (open) {
    focusWindow(open);
    return open;
  }
  const have = currentForm().modelId;
  if (have === null || !blockById(have)) chooseModel(defaultModel(app));
  const id = currentForm().modelId;
  return openWindow(EXPORT_WINDOW, exportTitle(id === null ? null : (blockById(id)?.name ?? null)), "normal", {}, ...viewport());
}

const notFound = (id: number) => refusal("block_model_not_found", `Блочная модель №${id} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");
const asError = (r: Refusal) => refusal(r.code, r.message, r.hint);

/** «Выбрать файл…»: the save window of the system (a path is the user's); null — cancelled. */
export async function chooseFile(form: ExportForm): Promise<string | null> {
  const name = form.modelId === null ? null : (blockById(form.modelId)?.name ?? null);
  const info = formatInfo(form.format);
  const path = (await bridge()?.pickSave(defaultFileName(name, form.format), info.filter)) ?? null;
  if (path) setForm({ path, pathFormat: form.format });
  return path;
}

/** The run of `blocks.export` with the arguments: the job followed to its end; the answer — the words about the file. */
export async function runExport(app: App, a: Args, source: CommandSource): Promise<string> {
  if (a.path === undefined || a.path === null || a.path === "") {
    // the file is chosen in the window; a model named in the call (the tree, «Свойства») is the model of the window
    const asked = Number(a.model_id);
    if (a.model_id !== undefined && a.model_id !== null) {
      if (!Number.isInteger(asked) || !blockById(asked)) throw notFound(asked);
      if (currentForm().modelId !== asked) chooseModel(asked);
    }
    openExportWindow(app);
    return "Открыто окно «Экспорт блочной модели»";
  }
  const got = bodyOfArgs(a, a.model_id === undefined ? null : fieldNamesOf(Number(a.model_id)));
  if (!got.ok) {
    setIssue(got.issue);
    throw refusal("bad_param", got.issue.message, got.issue.hint);
  }
  const model = blockById(got.modelId);
  if (!model) throw notFound(got.modelId);
  if (isRunning()) throw refusal("export_running", "Экспорт уже идёт.", "Дождитесь конца или отмените его в окне «Экспорт блочной модели».");
  exportStore.set({ form: got.form }); // the window shows what is asked, also when the call is refused
  if (got.form.format === "omf" && hasSubBlocks(got.modelId)) {
    const r: Refusal = { code: "omf_subblocks", message: `Блочная модель «${model.name}» с субблоками: OMF не хранит субблоки — экспорт возможен только после регуляризации.`, hint: "Выполните регуляризацию модели («Блочные модели» › «Регуляризация») и экспортируйте результат.", param: null };
    setIssue(issueOfRefusal(r, got.form));
    throw asError(r);
  }
  if (source === "user" || windowOfExport()) openExportWindow(app);
  const win = windowOfExport();
  if (win) updateWindow(win, { title: exportTitle(model.name) });
  const seq = beginRun(got.modelId, model.name);
  const fail = (r: Refusal): never => {
    patchRun(seq, { phase: "failed", error: r });
    setIssue(issueOfRefusal(r, got.form));
    throw asError(r);
  };
  const uid = projectStore.get().current?.uid ?? null;
  let started;
  try {
    started = await startExport(got.modelId, got.body, (files) => askConfirm(overwriteQuestion(files)));
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (!started) {
    patchRun(seq, { phase: "idle" });
    return "Отменено: файл не заменён";
  }
  patchRun(seq, { job: started });
  let end;
  try {
    end = await waitReport(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (currentRun().seq !== seq || (projectStore.get().current?.uid ?? null) !== uid) return "Проект изменился: экспорт больше не показывается";
  if (end.status === "cancelled") {
    patchRun(seq, { phase: "cancelled" });
    return `Экспорт модели «${model.name}» отменён: файл не создан`;
  }
  let job;
  try {
    job = await loadExportJob(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (end.status === "failed" || job.status === "failed") {
    const e = job.error ?? end.error;
    return fail({ code: e?.code ?? "job_failed", message: e?.message ?? "Экспорт не выполнен.", hint: e?.hint ?? "Повторите экспорт.", param: null });
  }
  if (!job.result) return fail({ code: "bad_answer", message: "Ядро не сказало, какой файл записан.", hint: "Проверьте файл в папке; если повторится — обновите программу.", param: null });
  patchRun(seq, { phase: "done", result: job.result });
  return resultText(model.name, job.result);
}

/** «Сохранить…» of the window: the file is chosen in the save window, then the command runs with the form. */
export async function saveFromWindow(app: App): Promise<void> {
  const form = currentForm();
  const path = (form.path && form.pathFormat === form.format ? form.path : null) ?? (await chooseFile(form));
  if (!path) return;
  await app.registry.execute("blocks.export", argsOfForm(currentForm(), path), "user");
}

/** «Отменить» of the window: the job of the run is cancelled between the portions; the file is not created. */
export async function cancelExport(): Promise<void> {
  const job = currentRun().job;
  if (job && isRunning()) await cancelJob(job);
}

export function bindExport(app: App): void {
  app.registry.bind(
    "blocks.export",
    noted(app, (a, source) => runExport(app, a, source)),
    (ctx) => needProject(ctx) ?? (blocksStore.get().loaded && !blocksStore.get().list.length ? "В проекте нет блочных моделей" : null),
  );
  // the export belongs to the project: another project — a clean window
  let uid: string | null = projectStore.get().current?.uid ?? null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetExport();
  });
  // a model that is gone leaves the form
  blocksStore.subscribe(() => {
    const id = currentForm().modelId;
    if (id !== null && blocksStore.get().loaded && !blockById(id)) setForm({ modelId: null, fields: [], allFields: true, path: null, pathFormat: null });
  });
}

/** The window for the shell's window layer (`shell/Shell.tsx`); null — not this window. */
export function renderExportWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== EXPORT_WINDOW) return null;
  return createElement(ExportWindow, { winId: w.id });
}
