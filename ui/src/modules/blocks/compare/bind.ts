/**
 * The command `estimate.compare` and the window «Сравнить оценки» of the shell (ТЗ 7.35 Д-37; ADR 0027 решения 25, 29): without the fields the command opens the window
 * (the model chosen in the tree, else the one the form has, else the first); with both fields it makes the request (every wrong thing is told in Russian with a hint),
 * starts the job `estimate.compare` (quick: «Чтение», «Расчёт», «Запись»), follows it («Отменить») and answers with the text of the result; the window shows the same run.
 * The window runs the command through the registry (the log, the notice, the question of the assistant). «Показать Δ в 3D» is two commands of the display of the block
 * models: `blocks.show` and `blocks.style` of the field Δ on a scale symmetric around 0. The command writes a field into the model, so a project open for reading refuses the run (the window still opens).
 * `bindEstimateCompare` answers the function that unbinds the listeners (one app at a time: the tests create several).
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../../shell/app";
import type { CommandSource } from "../../../shell/commands";
import { needProject, noted, refusal, viewport } from "../../../shell/objectRun";
import { focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../../shell/windows";
import { cancelJob } from "../../jobs/state";
import { projectStore } from "../../projects/state";
import { blockById, blocksStore, loadBlockDetails, loadBlocks } from "../state";
import { modelOf } from "../showCommands";
import { loadCompareJob, refusalOf, startCompare, waitCompareEnd, type Refusal } from "./api";
import { argsOfForm, bodyOfForm, formForModel, formOfArgs, resultText, symmetricRange } from "./model";
import { beginRun, COMPARE_TITLE, COMPARE_WINDOW, compareStore, currentForm, currentRun, isRunning, patchRun, resetCompare, setForm, setShown, syncCompare } from "./state";
import { EstimateCompareWindow } from "./EstimateCompareWindow";

/** The window «Сравнить оценки», if open. */
export const windowOfCompare = (): string | null => windowStore.get().windows.find((w) => w.kind === COMPARE_WINDOW)?.id ?? null;

export const compareTitle = (model: string | null): string => (model ? `${COMPARE_TITLE} · ${model}` : COMPARE_TITLE);

/** The model of the window: the one selected in the tree, else the one the form has, else the first of the project. */
export function defaultModel(app: App): number | null {
  const sel = app.store.get().selected;
  if (sel?.kind === "blocks" && blockById(sel.id)) return sel.id;
  const have = currentForm().modelId;
  if (have !== null && blockById(have)) return have;
  return blocksStore.get().list[0]?.id ?? null;
}

/** A model chosen in the window: the fields A and B and the domain go to the defaults of the model, the details of its fields (the list of the fields) are read. */
export function chooseModel(id: number | null): void {
  const model = id === null ? undefined : (blocksStore.get().details[id] ?? blockById(id));
  compareStore.set({ form: formForModel(id, model, currentForm()), note: null });
  const win = windowOfCompare();
  if (win) updateWindow(win, { title: compareTitle(id === null ? null : (blockById(id)?.name ?? null)) });
  if (id !== null)
    void loadBlockDetails(id).then(() => {
      if (currentForm().modelId === id) compareStore.set({ form: formForModel(id, blocksStore.get().details[id], currentForm()) });
    });
}

export function openCompareWindow(app: App, modelId?: number | null): string {
  syncCompare();
  const open = windowOfCompare();
  const want = modelId ?? (currentForm().modelId !== null && blockById(currentForm().modelId as number) ? currentForm().modelId : defaultModel(app));
  if (want !== currentForm().modelId || currentForm().modelId === null) chooseModel(want);
  if (open) {
    focusWindow(open);
    return open;
  }
  const cur = currentForm().modelId;
  return openWindow(COMPARE_WINDOW, compareTitle(cur === null ? null : (blockById(cur)?.name ?? null)), "normal", {}, ...viewport());
}

const asError = (r: Refusal) => refusal(r.code, r.message, r.hint);

/** The same arguments in a fixed order: the window runs the command with the arguments of its form, and keeps its form when it is the same. */
const same = (a: Record<string, unknown>, b: Record<string, unknown>): boolean => JSON.stringify(Object.entries(a).sort(([x], [y]) => (x < y ? -1 : 1))) === JSON.stringify(Object.entries(b).sort(([x], [y]) => (x < y ? -1 : 1)));

/** `estimate.compare {model_id?, field_a?, field_b?, domain?, out?}`. */
export async function runCompareCommand(app: App, a: Record<string, unknown>, source: CommandSource): Promise<string> {
  const noFields = a.field_a === undefined && a.field_b === undefined;
  if (noFields) {
    const model = a.model_id === undefined ? null : (await modelOf(app, a)).id;
    openCompareWindow(app, model);
    return `Открыто окно «${COMPARE_TITLE}»`;
  }
  if (a.field_a === undefined || a.field_b === undefined) throw refusal("bad_param", "Нужны оба поля: field_a и field_b.", "Укажите два числовых поля оценки модели или вызовите команду без полей: откроется окно выбора.");
  if (app.store.get().projectReadOnly) throw refusal("project_read_only", "Проект открыт только для чтения: поле Δ не записать.", "Откройте проект для записи; окно выбора полей открывается и так.");
  const model = await modelOf(app, a);
  const got = formOfArgs(a, model.id, currentForm());
  const built = bodyOfForm(got);
  if (!built.ok) throw refusal("bad_param", built.issue.message, built.issue.hint);
  if (isRunning()) throw refusal("compare_running", "Сравнение уже идёт.", `Дождитесь конца или отмените его в окне «${COMPARE_TITLE}».`);
  // the window shows what is asked (the form of the call); the form of the window itself stays as it is when it made the call
  const mine = bodyOfForm(currentForm());
  if (!(mine.ok && same(argsOfForm(currentForm()), argsOfForm(got)))) compareStore.set({ form: got, note: null });
  if (source === "user" || windowOfCompare()) openCompareWindow(app, model.id);
  const win = windowOfCompare();
  if (win) updateWindow(win, { title: compareTitle(model.name) });
  const seq = beginRun(model.id);
  const fail = (r: Refusal): never => {
    patchRun(seq, { phase: "failed", error: r });
    throw asError(r);
  };
  const uid = projectStore.get().current?.uid ?? null;
  let started;
  try {
    started = await startCompare(built.modelId, built.body);
  } catch (e) {
    return fail(refusalOf(e));
  }
  patchRun(seq, { job: started });
  let end;
  try {
    end = await waitCompareEnd(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (currentRun().seq !== seq || (projectStore.get().current?.uid ?? null) !== uid) return "Проект изменился: сравнение больше не показывается";
  if (end.status === "cancelled") {
    patchRun(seq, { phase: "cancelled" });
    return `Сравнение оценок модели «${model.name}» отменено: поле Δ не записано`;
  }
  if (end.status === "failed") {
    const e = end.error;
    return fail({ code: e?.code ?? "job_failed", message: e?.message ?? "Сравнение не выполнено.", hint: e?.hint ?? "Повторите сравнение.", param: null });
  }
  let done;
  try {
    done = await loadCompareJob(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (!done.result) return fail({ code: "bad_answer", message: "Ядро вернуло сравнение без результата.", hint: "Повторите сравнение; если повторится — обновите программу.", param: null });
  patchRun(seq, { phase: "done", result: done.result });
  await loadBlocks(); // the field Δ is in the list of the fields of the model now
  void loadBlockDetails(model.id);
  return resultText(done.result, model.name);
}

/** «Отменить» of the window: the job is cancelled between the stages; a cancelled run writes no field. */
export async function cancelCompare(): Promise<void> {
  const job = currentRun().job;
  if (job && isRunning()) await cancelJob(job);
}

/** «Показать Δ в 3D»: the model is shown and coloured by the field Δ on a scale symmetric around 0 (the colour of «no difference» is the middle). */
export async function showDelta(app: App): Promise<string> {
  const r = currentRun().result;
  if (!r || !r.field_written) throw refusal("no_field", "Поля Δ нет в модели.", "Выполните сравнение: поле Δ записывается в модель.");
  await loadBlocks();
  await loadBlockDetails(r.model_id);
  const range = symmetricRange(r.stats);
  const shown = await app.registry.execute("blocks.show", { model_id: r.model_id, visible: true }, "user");
  if (!shown.ok) return fail3d(shown.result);
  const style = await app.registry.execute("blocks.style", { model_id: r.model_id, field: r.field, kind: "scale", ...(range ?? {}) }, "user");
  if (!style.ok) return fail3d(style.result);
  const text = `Поле Δ «${r.field}» показано в 3D: шкала${range ? ` от ${range.from.toLocaleString("ru-RU")} до ${range.to.toLocaleString("ru-RU")}` : ""}, нулевая разность — середина шкалы; серые блоки — пропуск в A или B.`;
  setShown(`${r.model_id}:${r.field}`, text);
  return text;
}

function fail3d(text: string): string {
  setShown(null, text);
  return text;
}

export function bindEstimateCompare(app: App): () => void {
  app.registry.bind("estimate.compare", noted(app, (a, source) => runCompareCommand(app, a, source)), needProject);
  // the comparison belongs to the project: another project — a clean window
  let uid: string | null = projectStore.get().current?.uid ?? null;
  const off = projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetCompare();
  });
  // a model that is gone leaves the form (the result of it stays until the next run)
  const offModels = blocksStore.subscribe(() => {
    const id = currentForm().modelId;
    if (id !== null && blocksStore.get().loaded && !blockById(id)) setForm({ modelId: null, a: "", b: "", domain: "", out: "" });
  });
  return () => {
    off();
    offModels();
  };
}

/** The window for the shell's window layer (`shell/Shell.tsx`); null — not this window. */
export function renderEstimateCompareWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== COMPARE_WINDOW) return null;
  return createElement(EstimateCompareWindow, { winId: w.id });
}
