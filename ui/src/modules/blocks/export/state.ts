/**
 * What the window «Экспорт блочной модели» and the command `blocks.export` know (ТЗ 8.7.7; ADR 0023 §12): the form, the
 * first wrong thing of it with its place, the course of the run (the job, the result, the refusal). One window per project.
 * No React.
 */
import { Store } from "../../../shell/store";
import type { Started } from "./api";
import { EMPTY_FORM, nextFormat, type ExportForm, type ExportResult, type Format, type FormIssue, type Refusal } from "./model";

export const EXPORT_WINDOW = "blocks-export";

export type Phase = "idle" | "running" | "done" | "failed" | "cancelled";

export interface Run {
  /** grows with every run: an answer of an older run is dropped */
  seq: number;
  phase: Phase;
  modelId: number | null;
  model: string | null;
  job: Started | null;
  result: ExportResult | null;
  error: Refusal | null;
}

export const IDLE: Run = { seq: 0, phase: "idle", modelId: null, model: null, job: null, result: null, error: null };

export interface ExportState {
  form: ExportForm;
  issue: FormIssue | null;
  run: Run;
}

export const exportStore = new Store<ExportState>({ form: EMPTY_FORM, issue: null, run: IDLE });

export const currentForm = (): ExportForm => exportStore.get().form;
export const currentRun = (): Run => exportStore.get().run;
export const isRunning = (): boolean => exportStore.get().run.phase === "running";

export function setForm(patch: Partial<ExportForm>): void {
  exportStore.set((s) => ({ form: { ...s.form, ...patch }, issue: null }));
}

/** Another format: its encoding, its file (chosen again). */
export function changeFormat(format: Format): void {
  exportStore.set((s) => ({ form: nextFormat(s.form, format), issue: null }));
}

export function setIssue(issue: FormIssue | null): void {
  exportStore.set({ issue });
}

/** A new run: the form stays, the result of the earlier one goes. */
export function beginRun(modelId: number, model: string): number {
  const seq = exportStore.get().run.seq + 1;
  exportStore.set({ issue: null, run: { ...IDLE, seq, phase: "running", modelId, model } });
  return seq;
}

export function patchRun(seq: number, patch: Partial<Run>): void {
  if (exportStore.get().run.seq === seq) exportStore.set((s) => ({ run: { ...s.run, ...patch } }));
}

export function resetExport(): void {
  exportStore.set({ form: EMPTY_FORM, issue: null, run: { ...IDLE, seq: exportStore.get().run.seq + 1 } });
}
