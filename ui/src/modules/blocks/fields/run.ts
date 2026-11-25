/**
 * The run of an operation on a field (the calculator, the density, the categories, «Перестроить»; ТЗ 8.7.4, 8.7.5; ADR
 * 0023 §7, §9): start the job of the core over the version of the model the interface read, follow it (the stages,
 * «Отменить»), read the result, bring the list of models and the properties of the fields up to date. A field that is
 * already there is replaced only after a question in our own window (№ 69; the core answers 409 `field_exists` before
 * it starts anything); a call of the assistant is told to pass `replace`. A refusal is told in Russian with its hint and
 * its place in the form; a model changed elsewhere (409 `version_conflict`) is read again and the person repeats. No React.
 */
import type { App } from "../../../shell/app";
import { CoreError } from "../../../shell/api";
import type { CommandSource } from "../../../shell/commands";
import { askConfirm } from "../../../shell/dialogs";
import { refusal } from "../../../shell/objectRun";
import { cancelJob } from "../../jobs/state";
import { projectStore } from "../../projects/state";
import { waitJob, type Started } from "../../qaqc/setupApi";
import { blockById, loadBlocks } from "../state";
import { loadOpJob, refusalOf, type Refusal } from "./api";
import { issueOfRefusal, type Tab } from "./common";
import { beginRun, currentRun, fieldsStore, isRunning, patchRun, setIssue, type Op } from "./state";
import { summaryText } from "./text";

/** What a run needs: the job to start (over the version read; `replace` — the field may be replaced) and where its words go. */
export interface OpPlan {
  op: Op;
  tab: Tab;
  modelId: number;
  /** the field written (the title of the run) */
  field: string;
  /** the place of «поле уже есть» in the form */
  place: string;
  replace: boolean;
  /** the form's restrictor, to put a refusal about it in its place */
  restrictOn?: boolean;
  filterId?: number | null;
  start: (version: number, replace: boolean) => Promise<Started>;
}

const hooks = new Set<(modelId: number) => void>();

/** `fn(modelId)` runs after a write to a model (the properties of the fields read the list again). */
export function onFieldsWritten(fn: (modelId: number) => void): () => void {
  hooks.add(fn);
  return () => hooks.delete(fn);
}

export const notFound = (id: number) => refusal("block_model_not_found", `Блочная модель №${id} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");

export const READ_ONLY = refusal("project_read_only", "Проект открыт только для чтения: поля модели изменить нельзя.", "Закройте проект в другом окне и откройте его заново.");

const asError = (r: Refusal) => refusal(r.code, r.message, r.hint);

export const replaceQuestion = (field: string, model: string) => ({
  title: "Заменить поле",
  message: `Поле «${field}» в модели «${model}» уже есть. Заменить его новым? Прежние значения поля пропадут, единица и описание не переносятся.`,
  okLabel: "Заменить",
  danger: true,
});

/** The job of the plan followed to its end; the answer is the line of the log. */
export async function runOp(app: App, plan: OpPlan, source: CommandSource): Promise<string> {
  if (app.store.get().projectReadOnly) throw READ_ONLY;
  const model = blockById(plan.modelId);
  if (!model) throw notFound(plan.modelId);
  if (isRunning()) throw refusal("operation_running", "Поля уже считаются.", "Дождитесь конца или отмените расчёт в окне «Поля блочной модели».");
  const seq = beginRun(plan.op, plan.modelId, model.name, plan.field);
  const fail = (r: Refusal, place?: string): never => {
    patchRun(seq, { phase: "failed", error: r });
    const i = issueOfRefusal(plan.tab, r, plan.restrictOn, plan.filterId ?? null);
    setIssue(place ? { ...i, place } : plan.tab === "props" ? { ...i, place: plan.place } : i); // a row of the properties: the field it is about
    throw asError(r);
  };
  const uid = projectStore.get().current?.uid ?? null;
  let replace = plan.replace;
  let started: Started | null = null;
  for (let tries = 0; started === null && tries < 2; tries++) {
    try {
      started = await plan.start(blockById(plan.modelId)?.version ?? model.version, replace);
    } catch (e) {
      const r = refusalOf(e);
      if (r.code === "field_exists" && !replace) {
        const name = e instanceof CoreError && typeof e.body?.field === "string" ? e.body.field : plan.field;
        if (source === "user" && (await askConfirm(replaceQuestion(name, model.name)))) {
          replace = true;
          continue;
        }
        if (source === "user") {
          patchRun(seq, { phase: "cancelled" });
          return `Поле «${name}» оставлено без изменений`;
        }
        return fail({ ...r, hint: "Задайте другое имя или передайте replace: true, чтобы заменить поле." }, plan.place);
      }
      if (r.code === "version_conflict") {
        await loadBlocks().catch(() => undefined);
        return fail({ ...r, hint: `${r.hint} Данные модели обновлены — повторите действие.`.trim() });
      }
      return fail(r);
    }
  }
  if (started === null) return fail({ code: "operation_failed", message: "Не удалось начать расчёт.", hint: "Повторите действие.", param: null });
  patchRun(seq, { job: started });
  let end;
  try {
    end = await waitJob(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (currentRun().seq !== seq || (projectStore.get().current?.uid ?? null) !== uid) return "Проект изменился: результат больше не показывается";
  if (end.status === "cancelled") {
    patchRun(seq, { phase: "cancelled" });
    return `Расчёт поля «${plan.field}» отменён: в модель «${model.name}» ничего не записано`;
  }
  let full;
  try {
    full = await loadOpJob(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (end.status === "failed" || full.status === "failed") {
    const r: Refusal = full.error ?? { code: "job_failed", message: "Расчёт не удался.", hint: "Повторите расчёт.", param: null };
    if (r.code === "version_conflict") await loadBlocks().catch(() => undefined);
    return fail(r);
  }
  if (!full.result) return fail({ code: "bad_answer", message: "Ядро вернуло результат без поля.", hint: "Повторите расчёт; если повторится — обновите программу.", param: null });
  patchRun(seq, { phase: "done", result: full.result });
  await loadBlocks().catch(() => undefined);
  hooks.forEach((fn) => fn(plan.modelId));
  return summaryText(plan.op, full.result, model.name);
}

/** «Отменить» of the window: the job is cancelled between the portions; nothing is written (the field is committed whole or not at all). */
export async function cancelRun(): Promise<void> {
  const job = fieldsStore.get().run.job;
  if (job && isRunning()) await cancelJob(job);
}
