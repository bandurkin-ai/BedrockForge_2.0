/**
 * What the windows «Новый сценарий», «Экономика» and the commands `pit.*` know (ТЗ 8.11; ADR 0028 решения 20, 29): the scenarios of the project, the
 * current one (the one selected in the tree, else the one the windows work on) with its params and the answer of `validate` (the one source of
 * «Запустить»: the command is unavailable until it is `ready`, the hint is the first `missing`), the forms of the two windows, the course of the run.
 * The contract for the windows of 21.17 (rocks, slopes, calendar, check) and 21.18–21.19 (the panel, the tree, the surfaces, the comparison):
 * `pitStore`; `currentScenarioId(app)`; `selectScenario(id)` — reads the scenario and its readiness again; `scenarios` is read again on `pit.changed`.
 * No React.
 */
import type { App } from "../../shell/app";
import { Store } from "../../shell/store";
import { blockById, blocksStore } from "../blocks/state";
import { fieldsOf, type FieldInfo } from "../blocks/report/state";
import { projectStore } from "../projects/state";
import { loadDefaults, listScenarios, loadScenario, validateParams, type Refusal, type Started } from "./api";
import type { Defaults, ModelDefaults, RunSummary, ScenarioDetail, ScenarioRow, Validate } from "./answers";
import { EMPTY_ECONOMICS, EMPTY_SCENARIO, type EconomicsForm, type FormIssue, type ScenarioForm } from "./forms";

export const PIT_SCENARIO_WINDOW = "pit-scenario";
export const PIT_ECONOMICS_WINDOW = "pit-economics";
export const SCENARIO_TITLE = "Сценарий карьера";
export const ECONOMICS_TITLE = "Экономика карьера";

export type Phase = "idle" | "running" | "done" | "failed" | "cancelled";

export interface PitRun {
  phase: Phase;
  /** a run that was overtaken (another one started, the project changed) does not write its end */
  seq: number;
  scenarioId: number | null;
  scenarioName: string;
  job: Started | null;
  result: RunSummary | null;
  error: Refusal | null;
}

export const IDLE: PitRun = { phase: "idle", seq: 0, scenarioId: null, scenarioName: "", job: null, result: null, error: null };

export interface PitState {
  defaults: Defaults | null;
  /** `GET /pit/defaults?model_id=`: the fields the core proposes, the categories, the DTMs of the project — by the model */
  models: Record<number, ModelDefaults>;
  scenarios: ScenarioRow[];
  scenariosLoaded: boolean;
  /** the scenario the windows work on (the tree selection is read first: `currentScenarioId`) */
  currentId: number | null;
  scenario: ScenarioDetail | null;
  /** `validate` of `scenario.params`; null — not read yet */
  validate: Validate | null;
  scenarioForm: ScenarioForm;
  scenarioIssue: FormIssue | null;
  econForm: EconomicsForm;
  econIssue: FormIssue | null;
  run: PitRun;
}

const EMPTY: PitState = { defaults: null, models: {}, scenarios: [], scenariosLoaded: false, currentId: null, scenario: null, validate: null, scenarioForm: EMPTY_SCENARIO, scenarioIssue: null, econForm: EMPTY_ECONOMICS, econIssue: null, run: IDLE };

export const pitStore = new Store<PitState>(EMPTY);

export const currentRun = (): PitRun => pitStore.get().run;
export const isRunning = (): boolean => pitStore.get().run.phase === "running";
const projectUid = (): string | null => projectStore.get().current?.uid ?? null;

export function setScenarioForm(patch: Partial<ScenarioForm>): void {
  pitStore.set((s) => ({ scenarioForm: { ...s.scenarioForm, ...patch }, scenarioIssue: null }));
}

export function setEconForm(patch: Partial<EconomicsForm>): void {
  pitStore.set((s) => ({ econForm: { ...s.econForm, ...patch }, econIssue: null }));
}

export const setScenarioIssue = (i: FormIssue | null): void => pitStore.set({ scenarioIssue: i });
export const setEconIssue = (i: FormIssue | null): void => pitStore.set({ econIssue: i });

/** Another project: clean windows and lists (the defaults are the core's, they stay). */
export function resetPit(): void {
  detailSeq++;
  listSeq++;
  pitStore.set((s) => ({ ...EMPTY, defaults: s.defaults, run: { ...IDLE, seq: s.run.seq + 1 } }));
}

/** The fields of the model as the windows list them: the details (labels, statistics) when read, else the list of the models. */
export function modelFields(id: number | null): FieldInfo[] {
  if (id === null) return [];
  return fieldsOf(blocksStore.get().details[id] ?? blockById(id));
}

// ---- the run ----

/** A run begins: the old result goes (a result of another scenario must not stand beside the new one). */
export function beginRun(scenarioId: number, scenarioName: string): number {
  const seq = pitStore.get().run.seq + 1;
  pitStore.set({ run: { phase: "running", seq, scenarioId, scenarioName, job: null, result: null, error: null } });
  return seq;
}

/** Writes into the run `seq` (a newer run is not touched). */
export function patchRun(seq: number, patch: Partial<PitRun>): boolean {
  if (pitStore.get().run.seq !== seq) return false;
  pitStore.set((s) => ({ run: { ...s.run, ...patch } }));
  return true;
}

// ---- the lists and the defaults of the core ----

export async function ensureDefaults(): Promise<Defaults | null> {
  const have = pitStore.get().defaults;
  if (have) return have;
  try {
    const d = await loadDefaults();
    pitStore.set({ defaults: d });
    return d;
  } catch {
    return null; // the windows check against the built-in limits (the same numbers as the core's)
  }
}

/** The defaults of a model (the grades, the categories, the DTMs of the project, K by the grid); read once, again with `force`. */
export async function ensureModelDefaults(modelId: number, force = false): Promise<ModelDefaults | null> {
  const have = pitStore.get().models[modelId];
  if (have && !force) return have;
  try {
    const d = await loadDefaults(modelId);
    pitStore.set((s) => ({ defaults: s.defaults ?? d, models: d.model ? { ...s.models, [modelId]: d.model } : s.models }));
    return d.model;
  } catch {
    return null;
  }
}

let listSeq = 0;
let detailSeq = 0;

// every read is numbered: a series of `pit.changed` or another project must not let an older answer cover a newer one
export async function loadScenarios(): Promise<void> {
  const mine = ++listSeq;
  const uid = projectUid();
  const fresh = () => mine === listSeq && uid === projectUid();
  try {
    const rows = await listScenarios();
    if (fresh()) pitStore.set({ scenarios: rows, scenariosLoaded: true });
  } catch {
    if (fresh()) pitStore.set({ scenariosLoaded: true });
  }
}

/** The scenario `id` and its readiness (`validate` of its params); null — none (the windows then ask to create or to choose one). */
export async function selectScenario(id: number | null): Promise<ScenarioDetail | null> {
  const mine = ++detailSeq;
  const uid = projectUid();
  const fresh = () => mine === detailSeq && uid === projectUid();
  if (id === null) {
    pitStore.set({ currentId: null, scenario: null, validate: null });
    return null;
  }
  pitStore.set((s) => ({ currentId: id, scenario: s.scenario?.id === id ? s.scenario : null, validate: s.scenario?.id === id ? s.validate : null }));
  try {
    const detail = await loadScenario(id);
    if (!fresh()) return null;
    pitStore.set({ scenario: detail });
    const v = await validateParams(detail.params);
    if (fresh()) pitStore.set({ validate: v });
    return detail;
  } catch {
    // a scenario that is gone (deleted in another window) leaves the state; a failed read of a live one is told by the command that asked
    if (fresh()) pitStore.set({ currentId: null, scenario: null, validate: null });
    return null;
  }
}

/** What a `pit.changed` event means: the scenario `id` (or none), whether it is gone. */
export function pitEvent(data: string): { id: number | null; deleted: boolean } {
  try {
    const d = JSON.parse(data) as { id?: unknown; deleted?: unknown };
    return { id: typeof d.id === "number" ? d.id : null, deleted: d.deleted === true };
  } catch {
    return { id: null, deleted: false };
  }
}

export const scenarioById = (id: number): ScenarioRow | undefined => pitStore.get().scenarios.find((r) => r.id === id);

/** The scenario selected in the tree (`kind` is the tree's own: `pit_scenario`; the tree of 21.18 adds it to `ObjectKind` of the shell), else null. */
export function selectedScenario(app: App): number | null {
  const sel = app.store.get().selected as { kind: string; id: number } | null;
  return sel && (sel.kind === "pit_scenario" || sel.kind === "pit") ? sel.id : null;
}

/** The scenario the commands work on: `scenario_id` of the call is read by the caller; here the tree selection, else the one of the windows. */
export const currentScenarioId = (app: App): number | null => selectedScenario(app) ?? pitStore.get().currentId;

/** Why `pit.run` cannot run now, or null: no scenario, the scenario is not read yet, a missing parameter (the first `missing`), a run goes. */
export function runReason(app: App): string | null {
  const id = currentScenarioId(app);
  if (id === null) return "Выберите или создайте сценарий карьера";
  if (app.store.get().projectReadOnly) return "Проект открыт только для чтения";
  if (isRunning()) return "Сценарий уже считается";
  const s = pitStore.get();
  if (s.scenario?.id !== id || !s.validate) return "Сценарий загружается: подождите секунду";
  if (s.scenario.state === "running") return "Сценарий уже считается";
  if (!s.validate.ready) return s.validate.missing[0]?.message ?? "Сценарий не готов к расчёту";
  return null;
}
