/**
 * What the window «Поля блочной модели» and its commands know (ТЗ 8.7.4, 8.7.5; ADR 0023 §7, §9): the tab (the
 * calculator, the density, the categories, the properties of the fields), the model, the form of the calculator and the
 * last check of its expression, the run of an operation (the stage, the result, the refusal), the first wrong thing of
 * the forms. The forms of the density, the categories and the properties are in their own files. One window per
 * project; the form is not kept when the project changes. No React.
 */
import { Store } from "../../../shell/store";
import { blockById, blocksStore } from "../state";
import { fieldsOf, type FieldInfo } from "../report/state";
import type { CalcCheck, ExprIssue, OpResult, Refusal, Started } from "./api";
import { EMPTY_CALC, type CalcForm } from "./calcForm";
import type { Issue, Tab } from "./common";

export const FIELDS_WINDOW = "blocks-fields";

export const TAB_TITLES: Record<Tab, string> = { calc: "Калькулятор", density: "Плотность", classify: "Категории", props: "Свойства полей" };

/** What a run is: the three jobs that write a field, and «Перестроить» (the job that made the field once more). */
export type Op = "calc" | "density" | "classify" | "rebuild";

export type Phase = "idle" | "running" | "done" | "failed" | "cancelled";

export interface FieldRun {
  phase: Phase;
  /** a run that was overtaken (another one started, the project changed) does not write its end */
  seq: number;
  op: Op | null;
  modelId: number | null;
  modelName: string;
  /** the name of the field written (for the title of the run) */
  field: string;
  job: Started | null;
  result: OpResult | null;
  error: Refusal | null;
}

export const IDLE: FieldRun = { phase: "idle", seq: 0, op: null, modelId: null, modelName: "", field: "", job: null, result: null, error: null };

/** The last check of the expression of the calculator (`POST /blocks/calc/check`): what it says, for which text. */
export interface CheckState {
  phase: "idle" | "checking" | "ok" | "error";
  /** the model, the type and the text the answer is for: a changed text makes the answer old */
  key: string;
  result: CalcCheck | null;
  /** the place of the error in the text */
  issue: ExprIssue | null;
  /** another refusal (the model, the type): its words */
  failure: string | null;
}

export const NO_CHECK: CheckState = { phase: "idle", key: "", result: null, issue: null, failure: null };

export const checkKey = (modelId: number | null, dtype: string, expr: string): string => `${String(modelId)}|${dtype}|${expr}`;

export interface FieldsState {
  tab: Tab;
  modelId: number | null;
  calc: CalcForm;
  check: CheckState;
  run: FieldRun;
  issue: Issue | null;
}

export const EMPTY_STATE: FieldsState = { tab: "calc", modelId: null, calc: EMPTY_CALC, check: NO_CHECK, run: IDLE, issue: null };

export const fieldsStore = new Store<FieldsState>(EMPTY_STATE);

export const currentRun = (): FieldRun => fieldsStore.get().run;
export const isRunning = (): boolean => fieldsStore.get().run.phase === "running";
export const currentModel = (): number | null => fieldsStore.get().modelId;

export function setTab(tab: Tab): void {
  fieldsStore.set({ tab, issue: null });
}

export function setModelId(modelId: number | null): void {
  fieldsStore.set({ modelId, issue: null, check: NO_CHECK });
}

export function setCalc(patch: Partial<CalcForm>): void {
  fieldsStore.set((s) => ({ calc: { ...s.calc, ...patch }, issue: null }));
}

export const setIssue = (issue: Issue | null): void => fieldsStore.set({ issue });
export const setCheck = (check: CheckState): void => fieldsStore.set({ check });

/** The window's own state is gone (another project): everything empty, a run that goes on does not write its end. */
export function resetFields(): void {
  fieldsStore.set({ ...EMPTY_STATE, run: { ...IDLE, seq: fieldsStore.get().run.seq + 1 } });
}

/** A run begins: the old result goes (a result of another request must not stand beside the new form). */
export function beginRun(op: Op, modelId: number, modelName: string, field: string): number {
  const seq = fieldsStore.get().run.seq + 1;
  fieldsStore.set({ run: { phase: "running", seq, op, modelId, modelName, field, job: null, result: null, error: null }, issue: null });
  return seq;
}

/** Writes into the run `seq` (a newer run is not touched). */
export function patchRun(seq: number, patch: Partial<FieldRun>): boolean {
  if (fieldsStore.get().run.seq !== seq) return false;
  fieldsStore.set((s) => ({ run: { ...s.run, ...patch } }));
  return true;
}

/** The fields of a model as the forms list them: the details of the model (labels, statistics) when read, else the list. */
export function modelFields(id: number | null): FieldInfo[] {
  if (id === null) return [];
  return fieldsOf(blocksStore.get().details[id] ?? blockById(id));
}

/** The model has a field of this name (without case, as the core compares them). */
export const hasField = (modelId: number | null, name: string): boolean => modelFields(modelId).some((f) => f.name.toLowerCase() === name.trim().toLowerCase());
