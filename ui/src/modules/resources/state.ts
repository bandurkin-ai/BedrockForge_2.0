/**
 * What the window «Отчёт по ресурсам» and the commands `resources.*` know (ТЗ 8.10; ADR 0027): the form, the course of the run (stages,
 * the result, the refusal), the defaults and the templates of the core, the list of the saved reports. One window per project. The
 * contract for the result panel, the chart and the tree (20.13) and the templates window (20.14): `resourcesStore` — `run.result` is the
 * last result (the preview or the saved one), `reports` — the list of the saved ones (read again on `resources.changed`), `templates` —
 * the list of the forms; `RESOURCES_WINDOW` is the id of the window. No React.
 */
import { Store } from "../../shell/store";
import { blockById, blocksStore } from "../blocks/state";
import { fieldsOf, type FieldInfo } from "../blocks/report/state";
import { projectStore } from "../projects/state";
import { listReports, listTemplates, loadDefaults, type Defaults, type Refusal, type Started } from "./api";
import type { ReportListRow, ReportResult, TemplateRow } from "./answers";
import { EMPTY_FORM, type FormIssue, type ResourceForm } from "./form";

export const RESOURCES_WINDOW = "resources-report";
export const TITLE = "Отчёт по ресурсам";

export type Phase = "idle" | "running" | "done" | "failed" | "cancelled";

export interface ReportRun {
  phase: Phase;
  /** a run that was overtaken (another one started, the project changed) does not write its end */
  seq: number;
  modelId: number | null;
  modelName: string;
  job: Started | null;
  result: ReportResult | null;
  /** the run wrote the report to the project (`save`), else it is the preview */
  saved: boolean;
  error: Refusal | null;
}

export const IDLE: ReportRun = { phase: "idle", seq: 0, modelId: null, modelName: "", job: null, result: null, saved: false, error: null };

export interface ResourcesState {
  form: ResourceForm;
  run: ReportRun;
  issue: FormIssue | null;
  defaults: Defaults | null;
  templates: TemplateRow[];
  templatesLoaded: boolean;
  reports: ReportListRow[];
  reportsLoaded: boolean;
}

const EMPTY: ResourcesState = { form: EMPTY_FORM, run: IDLE, issue: null, defaults: null, templates: [], templatesLoaded: false, reports: [], reportsLoaded: false };

export const resourcesStore = new Store<ResourcesState>(EMPTY);

export const currentForm = (): ResourceForm => resourcesStore.get().form;
export const currentRun = (): ReportRun => resourcesStore.get().run;
export const isRunning = (): boolean => resourcesStore.get().run.phase === "running";

export function setForm(patch: Partial<ResourceForm>): void {
  resourcesStore.set((s) => ({ form: { ...s.form, ...patch }, issue: null }));
}

export const setIssue = (i: FormIssue | null): void => resourcesStore.set({ issue: i });

/** Another project: a clean window (the defaults and the templates are the core's, they stay). */
export function resetResources(): void {
  resourcesStore.set((s) => ({ ...EMPTY, defaults: s.defaults, templates: [], templatesLoaded: false, run: { ...IDLE, seq: s.run.seq + 1 } }));
}

/** A run begins: the old result goes (a result of another request must not stand beside the new form). */
export function beginRun(modelId: number, modelName: string, saved: boolean): number {
  const seq = resourcesStore.get().run.seq + 1;
  resourcesStore.set({ run: { phase: "running", seq, modelId, modelName, job: null, result: null, saved, error: null }, issue: null });
  return seq;
}

/** Writes into the run `seq` (a newer run is not touched). */
export function patchRun(seq: number, patch: Partial<ReportRun>): boolean {
  if (resourcesStore.get().run.seq !== seq) return false;
  resourcesStore.set((s) => ({ run: { ...s.run, ...patch } }));
  return true;
}

// ---- the fields of a model ----

/** The fields of the model as the window lists them: the details (labels, statistics) when read, else the list of the models. */
export function modelFields(id: number | null): FieldInfo[] {
  if (id === null) return [];
  return fieldsOf(blocksStore.get().details[id] ?? blockById(id));
}

/** The form of a model: what depends on the fields of the model (elements, the cut-off field, the density field, the keys) is kept only if the model has it. */
export function formForModel(id: number | null, fields: readonly FieldInfo[], keep: ResourceForm = EMPTY_FORM): ResourceForm {
  const same = keep.modelId === id;
  const has = (name: string, kind: FieldInfo["kind"]) => fields.some((f) => f.name === name && f.kind === kind);
  return {
    ...keep,
    modelId: id,
    elements: same ? keep.elements.filter((e) => has(e.field, "number")) : [],
    cutoffField: same && has(keep.cutoffField, "number") ? keep.cutoffField : "",
    densityMode: same ? keep.densityMode : "auto",
    densityField: same && has(keep.densityField, "number") ? keep.densityField : "",
    groups: same ? keep.groups.filter((k) => k.kind === "bench" || has(k.field, "category")) : [],
  };
}

// ---- the lists of the core ----

export async function ensureDefaults(): Promise<Defaults | null> {
  const have = resourcesStore.get().defaults;
  if (have) return have;
  try {
    const d = await loadDefaults();
    resourcesStore.set({ defaults: d });
    return d;
  } catch {
    return null; // the window checks against the built-in limits (the same numbers as the core's)
  }
}

let templatesSeq = 0;
let reportsSeq = 0;
const projectUid = (): string | null => projectStore.get().current?.uid ?? null;

// every read is numbered: a series of `resources.changed` or another project must not let an older answer cover a newer one
export async function loadTemplates(): Promise<void> {
  const mine = ++templatesSeq;
  const uid = projectUid();
  const fresh = () => mine === templatesSeq && uid === projectUid();
  try {
    const rows = await listTemplates();
    if (fresh()) resourcesStore.set({ templates: rows, templatesLoaded: true });
  } catch {
    if (fresh()) resourcesStore.set({ templatesLoaded: true });
  }
}

export async function loadReports(): Promise<void> {
  const mine = ++reportsSeq;
  const uid = projectUid();
  const fresh = () => mine === reportsSeq && uid === projectUid();
  try {
    const rows = await listReports();
    if (fresh()) resourcesStore.set({ reports: rows, reportsLoaded: true });
  } catch {
    if (fresh()) resourcesStore.set({ reportsLoaded: true });
  }
}

/** What a `resources.changed` event means for the list: `kind` of a report (or none) — read the reports again; a template — the templates. */
export function resourcesEvent(data: string): "report" | "template" {
  try {
    return (JSON.parse(data) as { kind?: unknown }).kind === "template" ? "template" : "report";
  } catch {
    return "report";
  }
}

export const reportById = (id: number): ReportListRow | undefined => resourcesStore.get().reports.find((r) => r.id === id);
