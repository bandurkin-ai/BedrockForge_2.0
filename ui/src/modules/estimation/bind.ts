/**
 * The handlers of `estimate.open`, `estimate.run`, `estimate.delete` and the window of the wizard «Оценка содержаний» (ТЗ 8.8.4;
 * ADR 0024 §10): without arguments a command opens its window (the model chosen in the tree, else the only one of the project);
 * with them it asks the core. The rows «Параметры оценки» follow the project: read when it opens and on `geostat.changed`
 * (`state.ts`); another project — a clean wizard. «Выполнить» asks «Заменить поля …?» when the core says `field_exists` and waits
 * for the job to its end (the stages are in «Задания»; the journal of the result is kept for the window «План оценки», `runState.ts`).
 * `estimate.plan` opens that window (a plan or a model), `estimate.plan_delete` deletes a plan, `estimate.run` without arguments
 * opens it too. `estimate.save` and `estimate.plan_save` write a row and a plan: the buttons of the wizard, of «Параметры оценки» and of the
 * plan window run these commands (the registry, the log, the keys, the assistant), they end in `saveWizard` and `writePlan`. One app at a time (the tests create several). The windows of the verification and the inspector bind their
 * commands themselves (18.23–18.25, 18.Д4); `estimate.ellipsoid` is bound by `previewBind.ts` (18.21).
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../shell/app";
import type { CommandSource } from "../../shell/commands";
import { askConfirm } from "../../shell/dialogs";
import { CoreError } from "../../shell/api";
import { needProject, needWritable, noted, refusal, viewport } from "../../shell/objectRun";
import { focusWindow, openWindow, windowStore, type FloatWin } from "../../shell/windows";
import { loadJob } from "../blocks/build/api";
import { blockById, blocksStore, loadBlockDetails, loadBlocks } from "../blocks/state";
import { onCoreEvent } from "../jobs/state";
import { projectStore } from "../projects/state";
import { readRun, refusalOf, startRun, waitJob, type RunBody } from "./api";
import { formOfArgs } from "./form";
import { PARAMS_TITLE, PARAMS_WINDOW, ParamsWindow } from "./ParamsWindow";
import { PlanWindow } from "./PlanWindow";
import { bindPreview } from "./previewBind";
import { choosePlan, chooseModel as choosePlanModel, confirmDiscard, deletePlan, pickModel, planById, planStore, PLAN_TITLE, PLAN_WINDOW, resetPlan, syncPlan, writePlan } from "./planState";
import { bindChecks, renderCheckWindow } from "./checksBind";
import { bindInspect, renderInspectWindow } from "./inspect/bind";
import { runBegin, runCancelled, runDone, runFailed, runJob, runReset } from "./runState";
import { clearEstimations, deleteEstimation, estimableModels, estimationStore, isEstimationEvent, loadRows, rowById } from "./state";
import { loadVariogramModels, putForm, resetWizard, saveWizard, setStep, startEdit, TITLE, WIZARD_WINDOW, wizardStore } from "./wizardState";
import { Wizard } from "./Wizard";
import { bindXval, renderXvalWindow } from "./xvalBind";

/** The window of the wizard, if open. */
export const wizardWindow = (): string | null => windowStore.get().windows.find((w) => w.kind === WIZARD_WINDOW)?.id ?? null;

export { estimableModels };

/** The model of a new wizard: the one selected in the tree, else the one the form has, else the only one of the project. */
function defaultModel(app: App, have: number | null): number | null {
  const list = estimableModels();
  const sel = app.store.get().selected;
  if (sel?.kind === "blocks" && list.some((m) => m.id === sel.id)) return sel.id;
  if (have !== null && list.some((m) => m.id === have)) return have;
  return list.length === 1 ? list[0].id : null;
}

/** The labels of the domain fields are in the details of the model («Свойства»). */
export function chooseModel(id: number | null): void {
  if (id !== null) void loadBlockDetails(id);
}

export async function openWizard(app: App, a: Record<string, unknown> = {}): Promise<string> {
  if (typeof a.id === "number") {
    if (!rowById(a.id) && estimationStore.get().loaded) throw refusal("estimation_not_found", `Строка «Параметры оценки» №${a.id} не найдена.`, "Номера строк — в разделе «Геостатистика → Параметры оценки» дерева.");
    await startEdit(a.id).catch((e: unknown) => {
      const r = refusalOf(e);
      throw refusal(r.code, r.message, r.hint);
    });
  } else {
    if (typeof a.model_id === "number" && !blockById(a.model_id) && blocksStore.get().loaded) throw refusal("block_model_not_found", `Блочная модель №${a.model_id} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");
    const f = formOfArgs(a, wizardStore.get().form);
    putForm(f.modelId === null ? { ...f, modelId: defaultModel(app, null) } : f, { dirty: wizardStore.get().dirty });
  }
  chooseModel(wizardStore.get().form.modelId);
  if (!wizardStore.get().vario.loaded) void loadVariogramModels();
  setStep(1);
  const open = wizardWindow();
  if (open) focusWindow(open);
  else openWindow(WIZARD_WINDOW, TITLE, "normal", {}, ...viewport());
  return `Открыт мастер «${TITLE}»`;
}

// ---- «Выполнить» ----

const idsOf = (text: unknown): number[] =>
  String(text ?? "")
    .split(/[;,\s]+/)
    .filter(Boolean)
    .map(Number);

/** The run of a plan or of rows: the job followed to its end; the answer is the text of the result. */
export async function runEstimate(a: Record<string, unknown>): Promise<string> {
  const planId = typeof a.plan_id === "number" ? a.plan_id : null;
  const ids = a.estimation_ids === undefined ? [] : idsOf(a.estimation_ids);
  if ((planId === null) === (ids.length === 0)) throw refusal("bad_param", "Укажите план или строки оценки (что-то одно).", "Передайте plan_id или estimation_ids, например 41; 42.");
  if (ids.some((n) => !Number.isInteger(n) || n < 1)) throw refusal("bad_param", "Номера строк оценки: нужны целые числа.", "Перечислите номера через «;», например 41; 42.");
  let modelId: number;
  // a row or a plan saved a moment ago (the wizard saves and runs at once) is not in the store until the list is read again
  const known = planId !== null ? estimationStore.get().plans.some((p) => p.id === planId) : ids.every((id) => rowById(id));
  if (!known) await loadRows();
  if (planId !== null) {
    const plan = estimationStore.get().plans.find((p) => p.id === planId);
    if (!plan) throw refusal("plan_not_found", `План оценки №${planId} не найден.`, "Номера планов — в окне «План оценки».");
    modelId = plan.model_id;
  } else {
    const rows = ids.map((id) => rowById(id));
    const missing = ids.filter((_, i) => !rows[i]);
    if (missing.length) throw refusal("estimation_not_found", `Не найдены строки «Параметры оценки»: ${missing.join(", ")}.`, "Номера строк — в разделе «Геостатистика → Параметры оценки» дерева.");
    const models = [...new Set(rows.map((r) => r!.model_id))];
    if (models.length !== 1) throw refusal("bad_param", "Строки относятся к разным блочным моделям.", "Выполняйте строки одной модели.");
    modelId = models[0];
  }
  const model = blockById(modelId);
  if (!model) throw refusal("block_model_not_found", `Блочная модель №${modelId} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");
  const body: RunBody = { ...(planId !== null ? { plan_id: planId } : { estimation_ids: ids }), expected_version: model.version, replace: a.replace === true };
  runBegin(modelId, model.name, planId, planId !== null ? (planById(planId)?.items ?? []) : ids);
  try {
    return await followRun(model, body);
  } catch (e) {
    runFailed(refusalOf(e));
    throw e;
  }
}

/** The job of the run from the start to the end: the question about the fields, the stages (`jobsStore`), the result kept for the window. */
async function followRun(model: { name: string }, body: RunBody): Promise<string> {
  let started;
  try {
    started = await startRun(body).catch(async (e: unknown) => {
      // the fields are in the model already: the person says «Заменить поля …?» (the same question for the assistant: it replaces data)
      if (e instanceof CoreError && e.code === "field_exists" && body.replace !== true) {
        const ok = await askConfirm({ title: "Заменить поля", message: `${e.message}\n\nПоля с префиксом строк будут записаны заново; остальные поля модели «${model.name}» не изменятся.`, okLabel: "Заменить", danger: true });
        if (!ok) throw new CoreError(0, "declined", "Оценка не запущена.", "Задайте другой префикс выходных полей или подтвердите замену.");
        return startRun({ ...body, replace: true });
      }
      throw e;
    });
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
  if (end.status === "cancelled") {
    const note = `Оценка модели «${model.name}» отменена: ничего не записано`;
    runCancelled(note);
    return note;
  }
  if (end.status === "failed") throw refusal(end.error?.code ?? "job_failed", end.error?.message ?? "Оценка не выполнена.", end.error?.hint ?? "Повторите оценку.");
  const done = await loadJob(started).catch((e: unknown) => {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  });
  const r = readRun(done.result);
  if (!r) throw refusal("bad_answer", "Ядро вернуло оценку без итогов.", "Повторите оценку; если повторится — обновите программу.");
  const summary = runSummary(model.name, r.fields, r.version, r.log);
  runDone(r, summary);
  void loadBlocks();
  void loadRows();
  return summary;
}

/** «Оценка записана: поля AU_OK_STATUS, … в модель «M» (версия 5). Проход 1: …» */
export function runSummary(model: string, fields: readonly string[], version: number, log: readonly string[]): string {
  const f = fields.length > 6 ? `${fields.slice(0, 6).join(", ")} и ещё ${fields.length - 6}` : fields.join(", ");
  return `Оценка записана в модель «${model}» (версия ${version}): ${f}.${log.length ? ` ${log.join(" ")}` : ""}`;
}

// ---- the deletion ----

export async function runDelete(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = a.id as number;
  const row = rowById(id);
  if (!row && estimationStore.get().loaded) throw refusal("estimation_not_found", `Строка «Параметры оценки» №${id} не найдена.`, "Номера строк — в разделе «Геостатистика → Параметры оценки» дерева.");
  const name = row?.name ?? `№${id}`;
  if (source === "user") {
    const plans = row?.plans.length ? ` Она уйдёт из планов (${row.plans.length}).` : "";
    if (!(await askConfirm({ title: "Удалить параметры оценки", message: `Удалить строку «${name}»?${plans} Поля, уже записанные в блочную модель, останутся.`, okLabel: "Удалить", danger: true }))) return "Отменено";
  }
  try {
    const r = await deleteEstimation(id);
    return `Строка «${name}» удалена${r.plans.length ? `; убрана из планов: ${r.plans.length}` : ""}`;
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
}

// ---- the save of a row and of a plan (the buttons of the windows run these commands) ----

/** `estimate.save {id?, as_new?}`: the form of the wizard or of «Параметры оценки» is written as a row (the same `saveWizard` as the buttons use). */
export async function runSave(a: Record<string, unknown>): Promise<string> {
  const s = wizardStore.get();
  if (!wizardWindow() && !paramsWindow()) throw refusal("no_wizard", "Нет открытой формы оценки: сохранять нечего.", "Откройте мастер «Оценка содержаний» или окно «Параметры оценки».");
  if (typeof a.id === "number" && a.id !== s.editing?.id) throw refusal("bad_param", `В форме открыта не строка №${a.id}.`, "Откройте строку командой «Параметры оценки» с этим id или уберите id: форма сохранится по своим правилам.");
  const saved = await saveWizard({ asNew: a.as_new === true });
  return `Сохранено: «${saved.name}», версия ${saved.version}`;
}

/** `estimate.plan_save {id?, model_id?, name?, items?, as_new?}`: a plan is written (the same `writePlan` as the buttons of the window use). */
export async function runPlanSave(a: Record<string, unknown>): Promise<string> {
  const id = typeof a.id === "number" ? a.id : null;
  const plan = id === null ? null : (planById(id) ?? null);
  if (id !== null && !plan && estimationStore.get().loaded) throw refusal("plan_not_found", `План оценки №${id} не найден.`, "Номера планов — в окне «План оценки».");
  const modelId = typeof a.model_id === "number" ? a.model_id : (plan?.model_id ?? null);
  if (modelId === null) throw refusal("bad_param", "Блочная модель не выбрана.", "Передайте model_id: номера моделей — в разделе «Блочные модели» дерева.");
  if (!blockById(modelId) && blocksStore.get().loaded) throw refusal("block_model_not_found", `Блочная модель №${modelId} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");
  const items = a.items === undefined ? null : idsOf(a.items);
  if (items?.some((n) => !Number.isInteger(n) || n < 1)) throw refusal("bad_param", "Номера строк плана: нужны целые числа.", "Перечислите номера через «;», например 41; 42.");
  const saved = await writePlan({ id, modelId, name: typeof a.name === "string" ? a.name : "", items, asNew: a.as_new === true });
  return `План «${saved.name}» сохранён (№${saved.id}, версия ${saved.version})`;
}

// ---- the window «Параметры оценки» ----

/** The window of the parameters of a row, if open. */
export const paramsWindow = (): string | null => windowStore.get().windows.find((w) => w.kind === PARAMS_WINDOW)?.id ?? null;

/**
 * `estimate.params {id?}`: the stored row on one page (the same form as the wizard's); without `id` — the row the form holds. A form
 * changed and not saved is not replaced without the person's word. The wizard and this window are two views of one form.
 */
export async function openParams(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const have = wizardStore.get();
  const id = typeof a.id === "number" ? a.id : (have.editing?.id ?? null);
  if (id === null) throw refusal("no_row", "Не указана строка «Параметры оценки».", "Передайте id строки из раздела «Геостатистика → Параметры оценки» дерева.");
  if (!rowById(id) && estimationStore.get().loaded) throw refusal("estimation_not_found", `Строка «Параметры оценки» №${id} не найдена.`, "Номера строк — в разделе «Геостатистика → Параметры оценки» дерева.");
  if (id !== have.editing?.id) {
    if (have.dirty && source === "user" && !(await askConfirm({ title: PARAMS_TITLE, message: "В мастере есть несохранённые изменения. Заменить их строкой из проекта?", okLabel: "Заменить", danger: true }))) return "Отменено";
    await startEdit(id).catch((e: unknown) => {
      const r = refusalOf(e);
      throw refusal(r.code, r.message, r.hint);
    });
    chooseModel(wizardStore.get().form.modelId);
  }
  if (!wizardStore.get().vario.loaded) void loadVariogramModels();
  const open = paramsWindow();
  if (open) focusWindow(open);
  else openWindow(PARAMS_WINDOW, PARAMS_TITLE, "normal", {}, ...viewport());
  return `Открыто окно «${PARAMS_TITLE}»`;
}

// ---- the window «План оценки» ----

/** The window of the plan, if open. */
export const planWindow = (): string | null => windowStore.get().windows.find((w) => w.kind === PLAN_WINDOW)?.id ?? null;

/**
 * `estimate.plan {plan_id?, model_id?}`: the window with a plan, or with the plans of a model; without arguments — as it was left, else
 * the model chosen in the tree (the only one of the project). A plan changed and not saved is not replaced without the person's word.
 */
export async function openPlan(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const planId = typeof a.plan_id === "number" ? a.plan_id : null;
  const modelId = typeof a.model_id === "number" ? a.model_id : null;
  const plan = planId === null ? null : (planById(planId) ?? null);
  if (planId !== null && !plan && estimationStore.get().loaded) throw refusal("plan_not_found", `План оценки №${planId} не найден.`, "Номера планов — в разделе «Геостатистика → Параметры оценки» дерева и в окне «План оценки».");
  if (modelId !== null && !blockById(modelId) && blocksStore.get().loaded) throw refusal("block_model_not_found", `Блочная модель №${modelId} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");
  const cur = planStore.get();
  const sel = app.store.get().selected;
  const model = plan?.model_id ?? modelId ?? (cur.modelId !== null ? null : pickModel(sel?.kind === "blocks" ? sel.id : null));
  const moves = plan ? plan.id !== cur.planId : model !== null && model !== cur.modelId;
  if (moves && source === "user" && !(await confirmDiscard())) return "Отменено";
  if (plan) choosePlan(plan.id);
  else if (model !== null && moves) choosePlanModel(model);
  const open = planWindow();
  if (open) focusWindow(open);
  else openWindow(PLAN_WINDOW, PLAN_TITLE, "normal", {}, ...viewport());
  return `Открыто окно «${PLAN_TITLE}»`;
}

export async function runPlanDelete(a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const id = a.id as number;
  const plan = planById(id);
  if (!plan && estimationStore.get().loaded) throw refusal("plan_not_found", `План оценки №${id} не найден.`, "Номера планов — в разделе «Геостатистика → Параметры оценки» дерева и в окне «План оценки».");
  const name = plan?.name ?? `№${id}`;
  if (source === "user" && !(await askConfirm({ title: "Удалить план оценки", message: `Удалить план «${name}»? Строки «Параметры оценки» и поля блочной модели останутся.`, okLabel: "Удалить", danger: true }))) return "Отменено";
  try {
    await deletePlan(id);
    return `План «${name}» удалён; строки остались`;
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
}

// ---- the registry ----

let unbind: (() => void)[] = [];

export function bindEstimation(app: App): void {
  unbind.forEach((fn) => fn());
  unbind = [];
  app.registry.bind("estimate.open", noted(app, (a) => openWizard(app, a)), needProject);
  // without a plan or rows `estimate.run` is the window where they are chosen (§12: a command without parameters opens its window)
  app.registry.bind("estimate.run", noted(app, (a, s) => (a.plan_id === undefined && a.estimation_ids === undefined ? openPlan(app, a, s) : runEstimate(a))), needWritable(app));
  app.registry.bind("estimate.delete", noted(app, (a, s) => runDelete(a, s)), needWritable(app));
  app.registry.bind("estimate.save", noted(app, (a) => runSave(a)), needWritable(app));
  app.registry.bind("estimate.plan_save", noted(app, (a) => runPlanSave(a)), needWritable(app));
  app.registry.bind("estimate.params", noted(app, (a, s) => openParams(a, s)), needProject);
  app.registry.bind("estimate.plan", noted(app, (a, s) => openPlan(app, a, s)), needProject);
  app.registry.bind("estimate.plan_delete", noted(app, (a, s) => runPlanDelete(a, s)), needWritable(app));
  unbind.push(bindChecks(app)); // estimate.kna, .global, .qq, .swath (18.24)
  unbind.push(...bindPreview(app)); // estimate.ellipsoid and the layers of the preview in 3D (18.21)
  bindInspect(app); // «Блок · i, j, k» (Д-30, 18.Д4): the command, the click on a block, the window
  let uid: string | null = projectStore.get().current?.uid ?? null;
  if (uid) void loadRows();
  unbind.push(
    ...bindXval(app), // «Перекрёстная проверка» (18.23): its command, its events, its window
    // what was asked of one project does not belong to another
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      clearEstimations();
      resetWizard();
      resetPlan();
      runReset();
      if (cur) void loadRows();
    }),
    // the window of the plan follows the lists and the models
    estimationStore.subscribe(syncPlan),
    blocksStore.subscribe(syncPlan),
    onCoreEvent((event, data) => {
      if (event === "geostat.changed" && projectStore.get().current && isEstimationEvent(data)) void loadRows();
    }),
    // a model that is gone leaves the form
    blocksStore.subscribe(() => {
      const id = wizardStore.get().form.modelId;
      if (blocksStore.get().loaded && id !== null && !blockById(id)) putForm({ ...wizardStore.get().form, modelId: null, domainField: "", label: "" }, { dirty: true });
    }),
  );
}

/** The windows for the shell's window layer (`shell/Shell.tsx`); null — not a window of the estimation. */
export function renderEstimationWindow(w: FloatWin): ReactNode | null {
  if (w.kind === WIZARD_WINDOW) return createElement(Wizard, { winId: w.id });
  if (w.kind === PARAMS_WINDOW) return createElement(ParamsWindow, { winId: w.id });
  return w.kind === PLAN_WINDOW ? createElement(PlanWindow, { winId: w.id }) : (renderCheckWindow(w) ?? renderXvalWindow(w) ?? renderInspectWindow(w));
}
