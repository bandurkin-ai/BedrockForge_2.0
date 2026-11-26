/**
 * What the window «Сравнить оценки» knows (ТЗ 7.35 Д-37; ADR 0027 решение 25): the form (the model, the fields A and B, the domain, the name of the field Δ), the
 * course of the run of the job `estimate.compare` (the stage, the result, the first refusal) and what the window put in 3D. One window per project; another project
 * is a clean window (the window asks it when it opens). A run that was overtaken (another one started, the project changed) does not write its end. No React.
 */
import { Store } from "../../../shell/store";
import { projectStore } from "../../projects/state";
import type { EstimateCompare, Refusal, Started } from "./api";

export const COMPARE_WINDOW = "estimate-compare";
export const COMPARE_TITLE = "Сравнить оценки";

export interface CompareForm {
  modelId: number | null;
  a: string;
  b: string;
  /** the field of the domains; empty — the core takes `domain`, else the role of the code or class, else one row «Все» */
  domain: string;
  /** the name of the field Δ; empty — `<A>_<B>_D` */
  out: string;
}

export const EMPTY_FORM: CompareForm = { modelId: null, a: "", b: "", domain: "", out: "" };

export type Phase = "idle" | "running" | "done" | "failed" | "cancelled";

export interface CompareRun {
  phase: Phase;
  seq: number;
  modelId: number | null;
  job: Started | null;
  result: EstimateCompare | null;
  error: Refusal | null;
}

export const IDLE: CompareRun = { phase: "idle", seq: 0, modelId: null, job: null, result: null, error: null };

export interface CompareState {
  form: CompareForm;
  run: CompareRun;
  /** the field Δ shown in 3D by the window (`modelId:field`); null — nothing */
  shown: string | null;
  /** the words under the button «Показать Δ в 3D» */
  note: string | null;
  uid: string | null;
}

const EMPTY: CompareState = { form: EMPTY_FORM, run: IDLE, shown: null, note: null, uid: null };

export const compareStore = new Store<CompareState>(EMPTY);

export const currentForm = (): CompareForm => compareStore.get().form;
export const currentRun = (): CompareRun => compareStore.get().run;
export const isRunning = (): boolean => compareStore.get().run.phase === "running";

export function setForm(patch: Partial<CompareForm>): void {
  compareStore.set((s) => ({ form: { ...s.form, ...patch }, note: null }));
}

/** A clean window of the project that is open now. */
export function resetCompare(): void {
  compareStore.set((s) => ({ ...EMPTY, run: { ...IDLE, seq: s.run.seq + 1 }, uid: projectStore.get().current?.uid ?? null }));
}

/** The window of another project is dropped. */
export function syncCompare(): void {
  if ((projectStore.get().current?.uid ?? null) !== compareStore.get().uid) resetCompare();
}

/** A run begins: the old result goes (a result of another request must not stand beside the new form). */
export function beginRun(modelId: number): number {
  const seq = compareStore.get().run.seq + 1;
  compareStore.set({ run: { phase: "running", seq, modelId, job: null, result: null, error: null }, shown: null, note: null });
  return seq;
}

/** Writes into the run `seq` (a newer run is not touched). */
export function patchRun(seq: number, patch: Partial<CompareRun>): boolean {
  if (compareStore.get().run.seq !== seq) return false;
  compareStore.set((s) => ({ run: { ...s.run, ...patch } }));
  return true;
}

export const setShown = (shown: string | null, note: string | null = null): void => compareStore.set({ shown, note });
