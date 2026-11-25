/**
 * The runs of the building of a block model (ТЗ 8.7.3, 8.7.5; ADR 0023 §6, §9, decisions 8, 9, 14): the handlers of
 * `blocks.prototype`, `blocks.fill`, `blocks.code`, `blocks.regularize`, `blocks.rebuild`. Without the arguments a command
 * opens its window; with them it checks them (Russian, with a hint and the place in the form: nothing wrong reaches the
 * core), shows them in the window, starts the job, follows it (the stages, «Отменить») and answers with the text of the
 * result. The window does everything through the registry (the log, the notice, the question of the assistant). A
 * cancel leaves nothing: every job writes last, whole or not at all. «Перестроить» asks first what goes away.
 */
import type { App } from "../../../shell/app";
import type { CommandSource } from "../../../shell/commands";
import { askConfirm, showMessage } from "../../../shell/dialogs";
import { refusal } from "../../../shell/objectRun";
import { cancelJob } from "../../jobs/state";
import { projectStore } from "../../projects/state";
import { blockById, blocksStore, loadBlocks } from "../state";
import {
  loadJob,
  loadModelFields,
  readFill,
  readPrototype,
  readRegularize,
  refusalOf,
  startCode,
  startFill,
  startPrototype,
  startRebuild,
  startRegularize,
  waitJob,
  type Refusal,
  type Started,
} from "./api";
import { bodyOfCode, bodyOfFill, codeFormOfArgs, fillFormOfArgs, issueOfFill } from "./fillForm";
import { fillSummary, prototypeSummary, rebuildQuestion, regularizeSummary } from "./format";
import { bodyOfPrototype, issueOfPrototype, prototypeFormOfArgs, type Args, type FormIssue } from "./forms";
import { bodyOfRegularize, issueOfRegularize, regFormOfArgs } from "./regForm";
import { beginRun, buildStore, form, isRunning, patchRun, putForm, runOf, setIssue, setRebuilding, type OpKind } from "./state";
import { openBuildWindow, windowOfKind } from "./bind";

const asError = (r: Refusal) => refusal(r.code, r.message, r.hint);

const notFound = (id: number) => refusal("block_model_not_found", `Блочная модель №${id} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");

/** A call with no argument at all opens the window (ADR 0021 п. 9). */
export const hasArgs = (a: Args): boolean => Object.keys(a).some((k) => a[k] !== undefined && a[k] !== null && a[k] !== "");

interface Plan<R> {
  kind: OpKind;
  /** the title of the progress: the model the run is for */
  label: string;
  start: () => Promise<Started>;
  read: (raw: unknown) => R | null;
  summary: (r: R) => string;
  issueOf: (r: Refusal) => FormIssue;
  /** what a cancel says: nothing is written */
  cancelled: string;
  /** the result is bad: the words */
  empty: string;
}

/** The run of a plan: the job followed to its end; the answer — the text of the result. */
async function drive<R>(app: App, p: Plan<R>, source: CommandSource): Promise<string> {
  const { kind } = p;
  if (isRunning(kind)) throw refusal(`${kind}_running`, "Эта операция уже выполняется.", "Дождитесь конца или отмените её в окне операции.");
  if (source === "user" || windowOfKind(kind)) openBuildWindow(app, kind);
  const seq = beginRun(kind, p.label);
  const fail = (r: Refusal): never => {
    patchRun(kind, seq, { phase: "failed", error: r });
    setIssue(kind, p.issueOf(r));
    throw asError(r);
  };
  const uid = projectStore.get().current?.uid ?? null;
  let started: Started;
  try {
    started = await p.start();
  } catch (e) {
    return fail(refusalOf(e));
  }
  patchRun(kind, seq, { job: started });
  let end;
  try {
    end = await waitJob(started);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (runOf(kind).seq !== seq || (projectStore.get().current?.uid ?? null) !== uid) return "Проект изменился: результат больше не показывается";
  if (end.status === "cancelled") {
    patchRun(kind, seq, { phase: "cancelled" });
    return p.cancelled;
  }
  if (end.status === "failed") {
    const e = end.error;
    return fail({ code: e?.code ?? "job_failed", message: e?.message ?? "Операция не выполнена.", hint: e?.hint ?? "Повторите операцию.", param: null });
  }
  let result: R | null;
  try {
    result = p.read((await loadJob(started)).result);
  } catch (e) {
    return fail(refusalOf(e));
  }
  if (!result) return fail({ code: "bad_answer", message: p.empty, hint: "Повторите операцию; если повторится — обновите программу.", param: null });
  patchRun(kind, seq, { phase: "done", result });
  void loadBlocks();
  return p.summary(result);
}

/** «Отменить» of a window: the job of the run is cancelled between the portions; nothing is written. */
export async function cancelOp(kind: OpKind): Promise<void> {
  const job = runOf(kind).job;
  if (job && isRunning(kind)) await cancelJob(job);
}

/** A wrong form or call: told in its place of the window, answered to the caller in Russian. */
function reject(kind: OpKind, issue: FormIssue): never {
  setIssue(kind, issue);
  throw refusal("bad_param", issue.message, issue.hint);
}

// ---- the prototype ----

export async function runPrototype(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!hasArgs(a)) {
    openBuildWindow(app, "prototype");
    return "Открыто окно «Прототип модели»";
  }
  const got = bodyOfPrototype(a);
  putForm("prototype", prototypeFormOfArgs(a, form("prototype")));
  if (!got.ok) return reject("prototype", got.issue);
  const label = got.body.name ?? "Прототип";
  return drive(app, { kind: "prototype", label, start: () => startPrototype(got.body), read: readPrototype, summary: prototypeSummary, issueOf: (r) => issueOfPrototype(r, form("prototype")), cancelled: `Прототип «${label}» отменён: ничего не записано`, empty: "Ядро вернуло прототип без сетки." }, source);
}

// ---- the fill ----

export async function runFill(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!hasArgs(a)) {
    openBuildWindow(app, "fill");
    return "Открыто окно «Заполнить каркасами»";
  }
  const got = bodyOfFill(a);
  putForm("fill", fillFormOfArgs(a, form("fill")));
  if (!got.ok) return reject("fill", got.issue);
  const proto = blockById(got.body.prototype_id);
  if (blocksStore.get().loaded && !proto) throw notFound(got.body.prototype_id);
  const label = proto?.name ?? `№${got.body.prototype_id}`;
  return drive(app, { kind: "fill", label, start: () => startFill(got.body), read: readFill, summary: (r) => fillSummary(r, "fill"), issueOf: issueOfFill, cancelled: `Заполнение по прототипу «${label}» отменено: ничего не записано`, empty: "Ядро вернуло заполнение без модели." }, source);
}

// ---- the coding ----

export async function runCode(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!hasArgs(a)) {
    openBuildWindow(app, "code");
    return "Открыто окно «Кодировать каркасами»";
  }
  const id = typeof a.model_id === "number" ? a.model_id : Number(a.model_id);
  const model = Number.isInteger(id) ? blockById(id) : undefined;
  if (blocksStore.get().loaded && Number.isInteger(id) && !model) throw notFound(id);
  const got = bodyOfCode(a, model?.version ?? 0);
  putForm("code", codeFormOfArgs(a, form("code")));
  if (!got.ok) return reject("code", got.issue);
  const label = model?.name ?? `№${got.modelId}`;
  return drive(app, { kind: "code", label, start: () => startCode(got.modelId, got.body), read: readFill, summary: (r) => fillSummary(r, "code"), issueOf: issueOfFill, cancelled: `Кодирование модели «${label}» отменено: ничего не записано`, empty: "Ядро вернуло кодирование без модели." }, source);
}

// ---- the regularization ----

export async function runRegularize(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!hasArgs(a)) {
    openBuildWindow(app, "regularize");
    return "Открыто окно «Регуляризация»";
  }
  const got = bodyOfRegularize(a);
  putForm("regularize", regFormOfArgs(a, form("regularize")));
  if (!got.ok) return reject("regularize", got.issue);
  const model = blockById(got.modelId);
  if (blocksStore.get().loaded && !model) throw notFound(got.modelId);
  const label = model?.name ?? `№${got.modelId}`;
  return drive(app, { kind: "regularize", label, start: () => startRegularize(got.modelId, got.body), read: readRegularize, summary: regularizeSummary, issueOf: issueOfRegularize, cancelled: `Регуляризация модели «${label}» отменена: ничего не записано`, empty: "Ядро вернуло регуляризацию без итогов." }, source);
}

// ---- «Перестроить» ----

/** The operations whose model can be built again: the fill, the coding with splitting, the regularization. */
export const REBUILDABLE = ["fill", "code", "regularize"];

const opOf = (id: number): string | null => ((blocksStore.get().details[id] ?? blockById(id))?.source as { op?: string } | null)?.op ?? null;

/** What «Перестроить» takes away (the fields other operations made) as the core will count it. */
export function droppedOf(modelOp: string | null, fields: readonly { name: string; op: string | null }[]): string[] {
  return fields.filter((f) => f.op !== null && f.op !== modelOp).map((f) => f.name);
}

export async function runRebuild(app: App, a: Args, source: CommandSource): Promise<string> {
  const sel = app.store.get().selected;
  const raw = a.model_id ?? (sel?.kind === "blocks" ? sel.id : undefined);
  const id = typeof raw === "number" ? raw : Number(raw);
  if (raw === undefined || !Number.isInteger(id)) throw refusal("bad_param", "Не выбрана блочная модель.", "Выберите модель в разделе «Блочные модели» дерева или передайте model_id.");
  const model = blockById(id);
  if (!model) throw notFound(id);
  const op = opOf(id);
  if (!op || !REBUILDABLE.includes(op)) throw refusal("not_rebuildable", `Модель «${model.name}» нельзя перестроить.`, "Перестраиваются модели, созданные заполнением, кодированием с дроблением и регуляризацией.");
  if (buildStore.get().rebuilding !== null) throw refusal("rebuild_running", "Другая модель уже перестраивается.", "Дождитесь конца в списке «Задания».");
  const info = await loadModelFields(id).catch((e: unknown) => {
    throw asError(refusalOf(e));
  });
  const dropped = droppedOf(op, info.fields);
  if (source === "user" && !(await askConfirm({ title: "Перестроить модель", message: rebuildQuestion(model.name, dropped, info.stale ? info.staleReason : null), okLabel: "Перестроить", danger: dropped.length > 0 }))) return "Отменено";
  setRebuilding(id);
  try {
    const version = typeof a.expected_version === "number" ? a.expected_version : info.version;
    const started = await startRebuild(id, version).catch((e: unknown) => {
      throw asError(refusalOf(e));
    });
    const end = await waitJob(started).catch((e: unknown) => {
      throw asError(refusalOf(e));
    });
    if (end.status === "cancelled") return `Перестройка модели «${model.name}» отменена: модель не изменена`;
    if (end.status === "failed") throw refusal(end.error?.code ?? "job_failed", end.error?.message ?? "Модель не перестроена.", end.error?.hint ?? "Повторите перестройку.");
    const done = await loadJob(started).catch((e: unknown) => {
      throw asError(refusalOf(e));
    });
    const r = (done.result ?? {}) as { version?: unknown; dropped?: unknown };
    const gone = Array.isArray(r.dropped) ? r.dropped.filter((x): x is string => typeof x === "string") : [];
    void loadBlocks();
    if (gone.length && source === "user") await showMessage({ title: "Модель перестроена", message: `Модель «${model.name}» построена заново (версия ${String(r.version ?? "?")}).\n\nУдалены поля других операций: ${gone.join(", ")}.` });
    return `Модель «${model.name}» перестроена${typeof r.version === "number" ? ` (версия ${r.version})` : ""}.${gone.length ? ` Удалены поля других операций: ${gone.join(", ")}.` : ""}`;
  } finally {
    setRebuilding(null);
  }
}

