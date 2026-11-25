/**
 * The handlers of the commands of the simulation (ТЗ 8.8.1; Д-23; docs/progress/e18-api.md §9, §12): `simulate.open`, `.sgs`, `.sis`,
 * `.cosgs` (the wizard, or — with the parameters — the run), `.save`, `.run`, `.summary`, `.post`, `.delete`. Without arguments a command
 * opens its window (the model chosen in the tree, else the only one of the project); with them it asks the core. The rows «Моделирование»
 * follow the project: read when it opens and on `geostat.changed` (`state.ts`); another project — a clean wizard. «Выполнить» follows
 * the job to its end (the stages are in «Задания»; the journal of the result is kept for the wizard, `runState.ts`). The post-processing
 * asks «Заменить поля …?» when the core says `field_exists`. One app at a time (the tests create several).
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { CoreError } from "../../shell/api";
import { needProject, needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { loadJob } from "../blocks/build/api";
import { blockById, blocksStore, loadBlocks } from "../blocks/state";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { estimableModels } from "../estimation/state";
import { PostWindow } from "./PostWindow";
import { SimWizard } from "./SimWizard";
import { SummaryWindow } from "./SummaryWindow";
import { readPost, readRun, refusalOf, startPost, startRun, waitJob, type PostBody, type PostStat, type RunBody, type SimKind, type Started } from "./api";
import { bodyOf, EMPTY_SIM_FORM, formOfArgs } from "./form";
import { choosePost, postBodyOf, postStore, POST_TITLE, POST_WINDOW, resetPost, sourceOf, type PostSource } from "./postState";
import { runBegin, runCancelled, runDone, runFailed, runJob, runReset, runStore, runSummary } from "./runState";
import { clearSimulations, deleteSimulation, isSimulationEvent, loadDetails, loadRows, simById, simulationStore } from "./state";
import { chooseSummary, summaryStore, SUMMARY_TITLE, SUMMARY_WINDOW } from "./summaryState";
import { chooseModel, form, loadVariogramModels, putForm, resetWizard, saveSimulation, setForm, setStep, SIM_TITLE, SIM_WIZARD_WINDOW, simWizardStore, startEdit } from "./wizardState";

/** The window of the wizard, if open. */
export const wizardWindow = (): string | null => windowStore.get().windows.find((w) => w.kind === SIM_WIZARD_WINDOW)?.id ?? null;

const NO_SIM = (id: number) => refusal("simulation_not_found", `Моделирование №${id} не найдено.`, "Номера строк — в разделе «Геостатистика → Моделирование» дерева.");
const NO_MODEL = (id: number) => refusal("block_model_not_found", `Блочная модель №${id} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");

/** The model of a new wizard: the one selected in the tree, else the one the form has, else the only one of the project. */
function defaultModel(app: App, have: number | null): number | null {
  const list = estimableModels();
  const sel = app.store.get().selected;
  if (sel?.kind === "blocks" && list.some((m) => m.id === sel.id)) return sel.id;
  if (have !== null && list.some((m) => m.id === have)) return have;
  return list.length === 1 ? list[0].id : null;
}

/** A row the arguments name: known to the store, else (the store is not read yet, or the row is new) the list is read again once. */
async function knownRow(id: number) {
  if (!simById(id)) await loadRows();
  const row = simById(id);
  if (!row && simulationStore.get().loaded) throw NO_SIM(id);
  return row;
}

// ---- the wizard ----

export async function openWizard(app: App, a: Record<string, unknown> = {}): Promise<string> {
  if (typeof a.id === "number") {
    await knownRow(a.id);
    await startEdit(a.id).catch((e: unknown) => {
      const r = refusalOf(e);
      throw refusal(r.code, r.message, r.hint);
    });
  } else {
    if (typeof a.model_id === "number" && !blockById(a.model_id) && blocksStore.get().loaded) throw NO_MODEL(a.model_id);
    const f = form();
    const kind: SimKind = a.kind === "sis" || a.kind === "cosgs" || a.kind === "sgs" ? a.kind : f.kind;
    const modelId = typeof a.model_id === "number" ? a.model_id : (f.modelId ?? defaultModel(app, null));
    const moved = modelId !== f.modelId;
    // another kind wants other models of variogram; another model — other domains
    // another kind is another row: the stored row of the old kind is not rewritten (the core refuses a SIS over the keys of an SGS)
    const detach = kind !== f.kind ? { editing: null, saved: null } : {};
    putForm({ ...f, kind, modelId, ...(moved ? { domainField: "", label: "" } : {}), ...(kind !== f.kind ? { variogramId: null, variogramIds: [] } : {}) }, { dirty: simWizardStore.get().dirty, ...detach });
  }
  chooseModel(form().modelId);
  if (!simWizardStore.get().vario.loaded) void loadVariogramModels();
  setStep(1);
  const open = wizardWindow();
  if (open) focusWindow(open);
  else openWindow(SIM_WIZARD_WINDOW, SIM_TITLE, "normal", {}, ...viewport());
  return `Открыт мастер «${SIM_TITLE}»`;
}

/** `simulate.sgs`, `.sis`, `.cosgs`: the wizard of the kind; with `id` the stored row runs; with the parameters they run as a new row. */
export async function kindCommand(app: App, kind: SimKind, a: Record<string, unknown>): Promise<string> {
  const asked = ["set_id", "table", "field", "variogram_id", "variogram_ids", "thresholds", "categories", "secondary_field"].some((k) => a[k] !== undefined);
  // the wizard opens in a read-only project too (to look); the run writes the realizations
  if (typeof a.id !== "number" && !asked) return openWizard(app, { kind, ...(typeof a.model_id === "number" ? { model_id: a.model_id } : {}) });
  if (app.store.get().projectReadOnly) throw refusal("project_read_only", "Проект открыт только для чтения.", "Откройте проект для записи: реализации сохраняются в проекте.");
  if (typeof a.id === "number") return runSimulation({ id: a.id });
  const f = formOfArgs(kind, a, { ...EMPTY_SIM_FORM, modelId: typeof a.model_id === "number" ? a.model_id : defaultModel(app, null) });
  const built = bodyOf(f, simWizardStore.get().vario);
  if (!built.ok) throw refusal(built.issue.code, built.issue.message, built.issue.hint);
  return runSimulation({ body: built.body });
}

// ---- «Выполнить» ----

/** The run of a stored row (`id`) or of a body (a new row the job writes last): the job followed to its end; the answer is the text of the result. */
export async function runSimulation(a: { id: number } | { body: Record<string, unknown> }): Promise<string> {
  const id = "id" in a ? a.id : null;
  const row = id === null ? undefined : await knownRow(id);
  if (runStore.get().phase === "running") throw refusal("busy", "Моделирование уже выполняется.", "Дождитесь конца задания или отмените его во вкладке «Задания».");
  const name = row?.name ?? (typeof (a as { body: { name?: unknown } }).body?.name === "string" ? String((a as { body: { name: string } }).body.name) : "Моделирование");
  runBegin(id, name);
  try {
    return await followRun(name, id === null ? { simulation: (a as { body: Record<string, unknown> }).body } : { simulation_id: id });
  } catch (e) {
    runFailed(refusalOf(e));
    throw e;
  }
}

/** The job of the run from the start to the end: the stages (`jobsStore`), the result kept for the wizard. */
async function followRun(name: string, body: RunBody): Promise<string> {
  let started: Started;
  try {
    started = await startRun(body);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  runJob(started);
  const uid = projectStore.get().current?.uid ?? null;
  let end;
  try {
    end = await waitJob(started);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  if ((projectStore.get().current?.uid ?? null) !== uid) {
    runReset();
    return "Проект изменился: результат больше не показывается";
  }
  void loadRows(); // a cancelled run leaves no realizations, but a row of a body may have been made before: the list is the truth
  if (end.status === "cancelled") {
    const note = `Моделирование «${name}» отменено: реализации не записаны`;
    runCancelled(note);
    return note;
  }
  if (end.status === "failed") throw refusal(end.error?.code ?? "job_failed", end.error?.message ?? "Моделирование не выполнено.", end.error?.hint ?? "Повторите моделирование.");
  const done = await loadJob(started).catch((e: unknown) => {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  });
  const r = readRun(done.result);
  if (!r) throw refusal("bad_answer", "Ядро вернуло моделирование без итогов.", "Повторите моделирование; если повторится — обновите программу.");
  const summary = runSummary(name, r);
  runDone(r, summary);
  return summary;
}

// ---- the save ----

/** `simulate.save {id?, as_new?}`: the form of the wizard is written as a row (the same `saveSimulation` as the button uses). */
export async function runSave(a: Record<string, unknown>): Promise<string> {
  const s = simWizardStore.get();
  if (!wizardWindow()) throw refusal("no_wizard", "Нет открытой формы моделирования: сохранять нечего.", "Откройте мастер «Моделирование».");
  if (typeof a.id === "number" && a.id !== s.editing?.id) throw refusal("bad_param", `В форме открыта не строка №${a.id}.`, "Откройте строку командой «Моделирование» с этим id или уберите id: форма сохранится по своим правилам.");
  const saved = await saveSimulation({ asNew: a.as_new === true });
  return `Сохранено: «${saved.name}», версия ${saved.version}`;
}

// ---- the windows of the summary and of the post-processing ----

/** The simulation a window is about: the one asked, else the one the window has, else the only one of the project. */
function pickSimulation(asked: unknown, have: number | null): number | null {
  if (typeof asked === "number") return asked;
  const rows = simulationStore.get().rows;
  if (have !== null && rows.some((r) => r.id === have)) return have;
  return rows.length === 1 ? rows[0].id : null;
}

export async function openSummary(a: Record<string, unknown>): Promise<string> {
  if (typeof a.id === "number") await knownRow(a.id);
  chooseSummary(pickSimulation(a.id, summaryStore.get().simId));
  const open = windowStore.get().windows.find((w) => w.kind === SUMMARY_WINDOW)?.id;
  if (open) focusWindow(open);
  else openWindow(SUMMARY_WINDOW, SUMMARY_TITLE, "normal", {}, ...viewport());
  return `Открыто окно «${SUMMARY_TITLE}»`;
}


function openPostWindow(id: number | null): string {
  if (id !== postStore.get().simId) choosePost(id);
  const open = windowStore.get().windows.find((w) => w.kind === POST_WINDOW)?.id;
  if (open) focusWindow(open);
  else openWindow(POST_WINDOW, POST_TITLE, "normal", {}, ...viewport());
  return `Открыто окно «${POST_TITLE}»`;
}

// ---- the post-processing ----

const STAT_WORDS = ["mean", "std", "p10", "p50", "p90", "prob", "mode"];

/** `simulate.post`: the window, or — with `run` or the parameters — the post-processing itself. */
export async function postCommand(a: Record<string, unknown>): Promise<string> {
  const runs = a.run === true || ["prefix", "stats", "thresholds", "replace"].some((k) => a[k] !== undefined);
  const id = pickSimulation(a.id, postStore.get().simId);
  if (typeof a.id === "number") await knownRow(a.id);
  if (!runs) return openPostWindow(id);
  if (id === null) throw refusal("no_simulation", "Не указано моделирование.", "Передайте id строки из раздела «Геостатистика → Моделирование» дерева.");
  return runPost(id, a);
}

/** The post-processing of the realizations of a row into the fields of its block model; the question «Заменить поля?» comes from the core's `field_exists`. */
export async function runPost(id: number, a: Record<string, unknown>): Promise<string> {
  const row = await knownRow(id);
  if (!row) throw NO_SIM(id);
  const info = simulationStore.get().details[id] ?? (await loadDetails(id));
  const src: PostSource = sourceOf(info?.meta ?? {});
  const model = blockById(row.model_id);
  if (!model) throw NO_MODEL(row.model_id);
  if (postStore.get().phase === "running") throw refusal("busy", "Постобработка уже выполняется.", "Дождитесь конца задания.");
  const stats = typeof a.stats === "string" ? a.stats.split(/[;,\s]+/).filter(Boolean) : null;
  const bad = stats?.find((s) => !STAT_WORDS.includes(s));
  if (bad) throw refusal("bad_param", `Статистика «${bad}» неизвестна.`, "Допустимы mean, std, p10, p50, p90, prob; для категорий — prob, mode.");
  const body = postBodyOf({ prefix: typeof a.prefix === "string" ? a.prefix : "", stats: stats as PostStat[] | null, thresholds: typeof a.thresholds === "string" ? a.thresholds : "", replace: a.replace === true }, src, model.version);
  if ("code" in body) {
    postStore.set({ simId: id, issue: body });
    throw refusal(body.code, body.message, body.hint);
  }
  postStore.set({ simId: id, phase: "running", job: null, result: null, error: null, issue: null });
  try {
    return await followPost(row.name, model.name, id, body);
  } catch (e) {
    const r = refusalOf(e);
    postStore.set(r.code === "declined" ? { phase: "idle", error: null } : { phase: "failed", error: r });
    throw e;
  }
}

async function followPost(name: string, modelName: string, id: number, body: PostBody): Promise<string> {
  let started: Started;
  try {
    started = await startPost(id, body).catch(async (e: unknown) => {
      if (e instanceof CoreError && e.code === "field_exists" && body.replace !== true) {
        const ok = await askConfirm({ title: "Заменить поля", message: `${e.message}\n\nПоля с этим префиксом будут записаны заново; остальные поля модели «${modelName}» не изменятся.`, okLabel: "Заменить", danger: true });
        if (!ok) throw new CoreError(0, "declined", "Постобработка не запущена.", "Задайте другой префикс полей или подтвердите замену.");
        return startPost(id, { ...body, replace: true });
      }
      throw e;
    });
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  postStore.set({ job: started });
  const uid = projectStore.get().current?.uid ?? null;
  let end;
  try {
    end = await waitJob(started);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  if ((projectStore.get().current?.uid ?? null) !== uid) {
    resetPost();
    return "Проект изменился: результат больше не показывается";
  }
  if (end.status === "cancelled") {
    postStore.set({ phase: "idle", job: null });
    return `Постобработка «${name}» отменена: поля не записаны`;
  }
  if (end.status === "failed") throw refusal(end.error?.code ?? "job_failed", end.error?.message ?? "Постобработка не выполнена.", end.error?.hint ?? "Повторите постобработку.");
  const done = await loadJob(started).catch((e: unknown) => {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  });
  const r = readPost(done.result);
  if (!r) throw refusal("bad_answer", "Ядро вернуло постобработку без итогов.", "Повторите постобработку; если повторится — обновите программу.");
  postStore.set({ phase: "done", result: r, job: null, error: null });
  void loadBlocks();
  const f = r.fields.length > 6 ? `${r.fields.slice(0, 6).join(", ")} и ещё ${r.fields.length - 6}` : r.fields.join(", ");
  return `Постобработка записана в модель «${modelName}» (версия ${r.version}): ${f}.`;
}

// ---- the deletion ----

export async function runDelete(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = a.id as number;
  const row = simById(id);
  if (!row && simulationStore.get().loaded) throw NO_SIM(id);
  const name = row?.name ?? `№${id}`;
  if (source === "user" && !(await askConfirm({ title: "Удалить моделирование", message: `Удалить «${name}» вместе с реализациями? Поля, уже записанные в блочную модель постобработкой, останутся.`, okLabel: "Удалить", danger: true }))) return "Отменено";
  try {
    await deleteSimulation(id);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  // what pointed at the row goes with it
  if (simWizardStore.get().editing?.id === id) simWizardStore.set({ editing: null, saved: null, dirty: true });
  if (summaryStore.get().simId === id) chooseSummary(null);
  if (postStore.get().simId === id) choosePost(null);
  return `«${name}» удалено вместе с реализациями`;
}

// ---- the registry ----

let unbind: (() => void)[] = [];

export function bindSimulation(app: App): void {
  unbind.forEach((fn) => fn());
  unbind = [];
  app.registry.bind("simulate.open", noted(app, (a) => openWizard(app, a)), needProject);
  for (const kind of ["sgs", "sis", "cosgs"] as const) app.registry.bind(`simulate.${kind}`, noted(app, (a) => kindCommand(app, kind, a)), needProject);
  app.registry.bind("simulate.save", noted(app, (a) => runSave(a)), needWritable(app));
  app.registry.bind("simulate.run", noted(app, (a) => runSimulation({ id: a.id as number })), needWritable(app));
  app.registry.bind("simulate.summary", noted(app, (a) => openSummary(a)), needProject);
  app.registry.bind("simulate.post", noted(app, (a) => postCommand(a)), needWritable(app));
  app.registry.bind("simulate.delete", noted(app, (a, s) => runDelete(a, s)), needWritable(app));
  let uid: string | null = projectStore.get().current?.uid ?? null;
  if (uid) void loadRows();
  unbind.push(
    // what was asked of one project does not belong to another
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      clearSimulations();
      resetWizard();
      runReset();
      resetPost();
      chooseSummary(null);
      if (cur) void loadRows();
    }),
    onCoreEvent((event, data) => {
      if (event === "geostat.changed" && projectStore.get().current && isSimulationEvent(data)) void loadRows();
    }),
    // a model that is gone leaves the form
    blocksStore.subscribe(() => {
      const id = form().modelId;
      if (blocksStore.get().loaded && id !== null && !blockById(id)) setForm({ modelId: null, domainField: "", label: "" });
    }),
  );
}

/** The windows for the shell's window layer (`shell/Shell.tsx`); null — not a window of the simulation. */
export function renderSimulationWindow(w: FloatWin): ReactNode | null {
  if (w.kind === SIM_WIZARD_WINDOW) return createElement(SimWizard, { winId: w.id });
  if (w.kind === SUMMARY_WINDOW) return createElement(SummaryWindow, { winId: w.id });
  return w.kind === POST_WINDOW ? createElement(PostWindow, { winId: w.id }) : null;
}
