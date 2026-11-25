/**
 * The handlers of `estimate.open`, `estimate.run`, `estimate.delete` and the window of the wizard «Оценка содержаний» (ТЗ 8.8.4;
 * ADR 0024 §10): without arguments a command opens its window (the model chosen in the tree, else the only one of the project);
 * with them it asks the core. The rows «Параметры оценки» follow the project: read when it opens and on `geostat.changed`
 * (`state.ts`); another project — a clean wizard. «Выполнить» asks «Заменить поля …?» when the core says `field_exists` and waits
 * for the job to its end (the stages are in «Задания»). One app at a time (the tests create several). The windows of the plan, the
 * verification and the inspector bind their commands themselves (18.21–18.25, 18.Д4).
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
import { clearEstimations, deleteEstimation, estimationStore, isEstimationEvent, loadRows, rowById } from "./state";
import { loadVariogramModels, putForm, resetWizard, setStep, startEdit, TITLE, WIZARD_WINDOW, wizardStore } from "./wizardState";
import { Wizard } from "./Wizard";

/** The window of the wizard, if open. */
export const wizardWindow = (): string | null => windowStore.get().windows.find((w) => w.kind === WIZARD_WINDOW)?.id ?? null;

/** The block models the estimation writes to: a regular step, with blocks. */
export const estimableModels = () => blocksStore.get().list.filter((m) => !m.grid.variable && m.cells > 0);

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
  const uid = projectStore.get().current?.uid ?? null;
  let end;
  try {
    end = await waitJob(started);
  } catch (e) {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  }
  if ((projectStore.get().current?.uid ?? null) !== uid) return "Проект изменился: результат больше не показывается";
  if (end.status === "cancelled") return `Оценка модели «${model.name}» отменена: ничего не записано`;
  if (end.status === "failed") throw refusal(end.error?.code ?? "job_failed", end.error?.message ?? "Оценка не выполнена.", end.error?.hint ?? "Повторите оценку.");
  const done = await loadJob(started).catch((e: unknown) => {
    const r = refusalOf(e);
    throw refusal(r.code, r.message, r.hint);
  });
  const r = readRun(done.result);
  if (!r) throw refusal("bad_answer", "Ядро вернуло оценку без итогов.", "Повторите оценку; если повторится — обновите программу.");
  void loadBlocks();
  void loadRows();
  return runSummary(model.name, r.fields, r.version, r.log);
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

// ---- the registry ----

let unbind: (() => void)[] = [];

export function bindEstimation(app: App): void {
  unbind.forEach((fn) => fn());
  unbind = [];
  app.registry.bind("estimate.open", noted(app, (a) => openWizard(app, a)), needProject);
  app.registry.bind("estimate.run", noted(app, (a) => runEstimate(a)), needWritable(app));
  app.registry.bind("estimate.delete", noted(app, (a, s) => runDelete(a, s)), needWritable(app));
  let uid: string | null = projectStore.get().current?.uid ?? null;
  if (uid) void loadRows();
  unbind.push(
    // what was asked of one project does not belong to another
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      clearEstimations();
      resetWizard();
      if (cur) void loadRows();
    }),
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
  return w.kind === WIZARD_WINDOW ? createElement(Wizard, { winId: w.id }) : null;
}
