/**
 * The handlers of the commands of the fields of a block model and the window «Поля блочной модели» of the shell (ТЗ
 * 8.7.4, 8.7.5; ADR 0023 §7, §9, §13). `blocks.calc` — the calculator: without the model and the expression it opens the
 * window on its tab; with them it checks the arguments (Russian, with a hint and the place in the form; nothing wrong
 * reaches the core), starts the job `blocks.calc` over the version of the model, follows it and answers with the line of
 * the log; the window shows the same run. `blocks.calc.check` only checks the expression (`POST /blocks/calc/check`): the type
 * of the result, the fields read, the canonical text — or the place of the error. The density, the categories and the
 * properties of the fields are in `opsBind.ts` and `propsBind.ts`; this file holds the window and what the tabs share.
 * The window does everything through the registry (the log, the notice, the question of the assistant). No React.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../../shell/app";
import type { CommandSource } from "../../../shell/commands";
import { needProject, noted, refusal, viewport } from "../../../shell/objectRun";
import { errorText } from "../../../shell/props";
import { focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../../shell/windows";
import { projectStore } from "../../projects/state";
import { blockById, blocksStore, loadBlockDetails } from "../state";
import { checkExpr, exprIssueOf, startCalc, type Dtype } from "./api";
import { formOfArgs, requestOfArgs } from "./calcForm";
import type { Args, Tab } from "./common";
import { CalcWindow } from "./CalcWindow";
import { notFound, runOp } from "./run";
import { checkKey, currentModel, fieldsStore, FIELDS_WINDOW, NO_CHECK, resetFields, setCheck, setIssue, setModelId, setTab, TAB_TITLES } from "./state";

export const fieldsTitle = (tab: Tab, model: string | null): string => (model ? `${TAB_TITLES[tab]} · ${model}` : TAB_TITLES[tab]);

/** The window «Поля блочной модели», if open. */
export const windowOfFields = (): string | null => windowStore.get().windows.find((w) => w.kind === FIELDS_WINDOW)?.id ?? null;

/** The model of the window: the one selected in the tree, else the one the window has, else the first of the project. */
export function defaultModel(app: App): number | null {
  const sel = app.store.get().selected;
  if (sel?.kind === "blocks" && blockById(sel.id)) return sel.id;
  const have = currentModel();
  if (have !== null && blockById(have)) return have;
  return blocksStore.get().list[0]?.id ?? null;
}

function retitle(): void {
  const win = windowOfFields();
  if (!win) return;
  const id = currentModel();
  updateWindow(win, { title: fieldsTitle(fieldsStore.get().tab, id === null ? null : (blockById(id)?.name ?? null)) });
}

/** A model chosen in the window; the details of its fields (labels, statistics) are read for the lists. */
export function chooseModel(id: number | null): void {
  setModelId(id);
  retitle();
  if (id !== null) void loadBlockDetails(id).then(() => retitle());
}

/** Opens the window (focused when open) on the tab; the model is the one chosen in the tree when the window had none. */
export function openFieldsWindow(app: App, tab?: Tab, modelId?: number | null): string {
  if (modelId !== undefined && modelId !== null && blockById(modelId)) chooseModel(modelId);
  else if (currentModel() === null || !blockById(currentModel() as number)) chooseModel(defaultModel(app));
  if (tab) setTab(tab);
  const open = windowOfFields();
  if (open) {
    retitle();
    focusWindow(open);
    return open;
  }
  const id = currentModel();
  return openWindow(FIELDS_WINDOW, fieldsTitle(fieldsStore.get().tab, id === null ? null : (blockById(id)?.name ?? null)), "normal", {}, ...viewport());
}

// ---- the check of the expression ----

let checkSeq = 0;

/** Checks the expression of the form on the core (`POST /blocks/calc/check`); the state says what the core said for which text. */
export async function checkNow(): Promise<void> {
  const s = fieldsStore.get();
  const id = s.modelId;
  const expr = s.calc.expr;
  const key = checkKey(id, s.calc.dtype, expr);
  if (id === null || !expr.trim()) return setCheck(NO_CHECK);
  const seq = ++checkSeq;
  setCheck({ ...NO_CHECK, phase: "checking", key });
  try {
    const result = await checkExpr(id, expr, s.calc.dtype || null);
    if (seq === checkSeq) setCheck({ phase: "ok", key, result, issue: null, failure: null });
  } catch (e) {
    if (seq !== checkSeq) return;
    const issue = exprIssueOf(e);
    setCheck({ phase: "error", key, result: null, issue, failure: issue ? null : errorText(e) });
  }
}

const KIND_TEXT = { number: "число", bool: "условие (0 или 1)", category: "категория" } as const;

/** «Только проверить»: the line the log and the assistant read — the kind of the result and the canonical text, or the place of the error. */
async function checkOnly(modelId: number, expr: string, dtype: Dtype | undefined): Promise<string> {
  fieldsStore.set((s) => ({ calc: { ...s.calc, expr, dtype: dtype ?? "" } }));
  await checkNow();
  const c = fieldsStore.get().check;
  if (c.phase === "ok" && c.result) {
    const used = c.result.fields_used.length ? c.result.fields_used.join(", ") : "нет";
    return `Выражение верно: результат — ${KIND_TEXT[c.result.out_kind]}, тип поля ${c.result.dtype}; читает поля: ${used}; запись: ${c.result.text}`;
  }
  const i = c.issue;
  if (i) throw refusal(i.code, i.message, i.hint);
  throw refusal("check_failed", c.failure ?? "Выражение не проверено.", `Модель №${modelId}: повторите проверку.`);
}

/** `blocks.calc.check`: the expression on the model, nothing written. */
export async function runCheck(app: App, a: Args, source: CommandSource): Promise<string> {
  const got = requestOfArgs(a, true);
  if (!got.ok) {
    setIssue(got.issue);
    throw refusal("bad_param", got.issue.message, got.issue.hint);
  }
  const { modelId, body } = got.request;
  if (!blockById(modelId)) throw notFound(modelId);
  fieldsStore.set((s) => ({ calc: formOfArgs(a, { ...s.calc, dtype: "" }) }));
  if (source === "user" || windowOfFields()) openFieldsWindow(app, "calc", modelId);
  else chooseModel(modelId);
  return checkOnly(modelId, body.expr, body.dtype);
}

export async function runCalc(app: App, a: Args, source: CommandSource): Promise<string> {
  if (a.model_id === undefined || a.model_id === null) {
    openFieldsWindow(app, "calc");
    return "Открыто окно «Поля блочной модели»: калькулятор";
  }
  const got = requestOfArgs(a);
  if (!got.ok) {
    setIssue(got.issue);
    throw refusal("bad_param", got.issue.message, got.issue.hint);
  }
  const { modelId, body } = got.request;
  const model = blockById(modelId);
  if (!model) throw notFound(modelId);
  fieldsStore.set((s) => ({ calc: formOfArgs(a, { ...s.calc, dtype: "" }) }));
  if (source === "user" || windowOfFields()) openFieldsWindow(app, "calc", modelId);
  else chooseModel(modelId);
  const key = checkKey(modelId, fieldsStore.get().calc.dtype, body.expr);
  return runOp(
    app,
    {
      op: "calc",
      tab: "calc",
      modelId,
      field: body.field,
      place: "field",
      replace: body.replace === true,
      restrictOn: body.restrict !== undefined,
      filterId: body.filter_id ?? null,
      start: async (version, replace) => {
        try {
          return await startCalc(modelId, { ...body, expected_version: version, ...(replace ? { replace: true } : {}) });
        } catch (e) {
          const issue = exprIssueOf(e);
          if (issue) setCheck({ phase: "error", key, result: null, issue, failure: null });
          throw e;
        }
      },
    },
    source,
  );
}

export function bindFields(app: App): void {
  const r = app.registry;
  r.bind(
    "blocks.calc",
    noted(app, (a, source) => runCalc(app, a, source)),
    (ctx) => needProject(ctx) ?? (blocksStore.get().loaded && !blocksStore.get().list.length ? "В проекте нет блочных моделей" : null),
  );
  r.bind("blocks.calc.check", noted(app, (a, source) => runCheck(app, a, source)), needProject);
  // the form belongs to the project: another project — a clean window
  let uid: string | null = projectStore.get().current?.uid ?? null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetFields();
  });
  // a model that is gone leaves the window (the result of the last run stays until the next one)
  blocksStore.subscribe(() => {
    const id = currentModel();
    if (id !== null && blocksStore.get().loaded && !blockById(id)) setModelId(null);
  });
}

/** The window for the shell's window layer (`shell/Shell.tsx`); null — not this window. */
export function renderFieldsWindow(w: FloatWin): ReactNode | null {
  if (w.kind !== FIELDS_WINDOW) return null;
  return createElement(CalcWindow, { winId: w.id });
}
