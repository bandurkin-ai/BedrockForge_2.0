/**
 * What the windows of the building of a block model and their commands know (ТЗ 8.7.3, 8.7.5; ADR 0023 §6, §9): the four
 * forms («Прототип модели», «Заполнить каркасами», «Кодировать каркасами», «Регуляризация»), the preview of the
 * prototype (the grid, the number of parents, the disk, the frame — `POST /blocks/prototype/preview`), the course of the
 * run of each operation (the job, the result, the refusal) and the first wrong thing of each form. One window of a kind
 * per project. No React.
 */
import { Store } from "../../../shell/store";
import { blockById, blocksStore } from "../state";
import type { FillResult, Preview, PrototypeResult, RegularizeResult, Refusal, Started } from "./api";
import { EMPTY_CODE, EMPTY_FILL, type CodeForm, type FillForm } from "./fillForm";
import { EMPTY_PROTOTYPE, type FormIssue, type PrototypeForm } from "./forms";
import { EMPTY_REG, type RegForm } from "./regForm";

export const PROTOTYPE_WINDOW = "blocks-prototype";
export const FILL_WINDOW = "blocks-fill";
export const CODE_WINDOW = "blocks-code";
export const REGULARIZE_WINDOW = "blocks-regularize";

export type OpKind = "prototype" | "fill" | "code" | "regularize";
export const OP_WINDOWS: Record<OpKind, string> = { prototype: PROTOTYPE_WINDOW, fill: FILL_WINDOW, code: CODE_WINDOW, regularize: REGULARIZE_WINDOW };

export interface Forms {
  prototype: PrototypeForm;
  fill: FillForm;
  code: CodeForm;
  regularize: RegForm;
}

export type Phase = "idle" | "running" | "done" | "failed" | "cancelled";

/** The run of an operation: a run that was overtaken (another one started, the project changed) does not write its end. */
export interface Run<R> {
  phase: Phase;
  seq: number;
  /** the name of the model the run is for (the title of the progress) */
  label: string;
  job: Started | null;
  result: R | null;
  error: Refusal | null;
}

export interface Runs {
  prototype: Run<PrototypeResult>;
  fill: Run<FillResult>;
  code: Run<FillResult>;
  regularize: Run<RegularizeResult>;
}

const idle = <R>(): Run<R> => ({ phase: "idle", seq: 0, label: "", job: null, result: null, error: null });

export type PreviewPhase = "idle" | "loading" | "ok" | "error";

export interface PreviewState {
  phase: PreviewPhase;
  /** the request the state is of (JSON): a new request overtakes the old one */
  key: string;
  seq: number;
  data: Preview | null;
  error: Refusal | null;
}

export interface BuildState {
  forms: Forms;
  runs: Runs;
  issues: Record<OpKind, FormIssue | null>;
  preview: PreviewState;
  /** the model «Перестроить» works on now */
  rebuilding: number | null;
}

const EMPTY_PREVIEW: PreviewState = { phase: "idle", key: "", seq: 0, data: null, error: null };

const fresh = (): BuildState => ({
  forms: { prototype: EMPTY_PROTOTYPE, fill: EMPTY_FILL, code: EMPTY_CODE, regularize: EMPTY_REG },
  runs: { prototype: idle(), fill: idle(), code: idle(), regularize: idle() },
  issues: { prototype: null, fill: null, code: null, regularize: null },
  preview: EMPTY_PREVIEW,
  rebuilding: null,
});

export const buildStore = new Store<BuildState>(fresh());

export const form = <K extends OpKind>(kind: K): Forms[K] => buildStore.get().forms[kind];
export const runOf = <K extends OpKind>(kind: K): Runs[K] => buildStore.get().runs[kind];
export const isRunning = (kind: OpKind): boolean => buildStore.get().runs[kind].phase === "running";

/** A change of the form (the person types): the old refusal of the form goes. */
export function setForm<K extends OpKind>(kind: K, patch: Partial<Forms[K]>): void {
  buildStore.set((s) => ({ forms: { ...s.forms, [kind]: { ...s.forms[kind], ...patch } }, issues: { ...s.issues, [kind]: null } }));
}

/** The whole form replaced (a call of the command shows what it asks). */
export function putForm<K extends OpKind>(kind: K, value: Forms[K]): void {
  buildStore.set((s) => ({ forms: { ...s.forms, [kind]: value }, issues: { ...s.issues, [kind]: null } }));
}

export const setIssue = (kind: OpKind, i: FormIssue | null): void => buildStore.set((s) => ({ issues: { ...s.issues, [kind]: i } }));

/** A run begins: the old result goes (a result of another request must not stand beside the new form). */
export function beginRun(kind: OpKind, label: string): number {
  const seq = buildStore.get().runs[kind].seq + 1;
  buildStore.set((s) => ({ runs: { ...s.runs, [kind]: { phase: "running", seq, label, job: null, result: null, error: null } }, issues: { ...s.issues, [kind]: null } }));
  return seq;
}

/** Writes into the run `seq` of `kind` (a newer run is not touched). */
export function patchRun(kind: OpKind, seq: number, patch: Partial<Run<unknown>>): boolean {
  if (buildStore.get().runs[kind].seq !== seq) return false;
  buildStore.set((s) => ({ runs: { ...s.runs, [kind]: { ...s.runs[kind], ...patch } } as Runs }));
  return true;
}

export function setPreview(patch: Partial<PreviewState>): void {
  buildStore.set((s) => ({ preview: { ...s.preview, ...patch } }));
}

export const setRebuilding = (id: number | null): void => buildStore.set({ rebuilding: id });

/** Another project: clean forms and runs (what was asked of one project does not belong to another). */
export function resetBuild(): void {
  const old = buildStore.get();
  const next = fresh();
  const seqs = (Object.keys(next.runs) as OpKind[]).reduce((acc, k) => ({ ...acc, [k]: { ...next.runs[k], seq: old.runs[k].seq + 1 } }), {} as Runs);
  buildStore.set({ ...next, runs: seqs, preview: { ...EMPTY_PREVIEW, seq: old.preview.seq + 1 } });
}

// ---- the models of the project the forms choose from ----

/** A prototype: a model with no rows made by the prototype (`source.op: prototype`, 0 rows). */
export const isPrototype = (m: { source: unknown } | undefined): boolean => !!m && (m.source as { op?: string } | null)?.op === "prototype";

export const prototypes = () => blocksStore.get().list.filter((m) => isPrototype(m));

/** The models that have blocks (the coding, the regularization). */
export const withBlocks = () => blocksStore.get().list.filter((m) => !isPrototype(m) && m.cells > 0);

export const modelName = (id: number | null): string => (id === null ? "" : (blockById(id)?.name ?? ""));
