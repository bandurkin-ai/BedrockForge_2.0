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
import { loadDefaults, listScenarios, loadScenario, refusalOf, validateParams, type Refusal, type Started } from "./api";
import type { Defaults, ModelDefaults, RunSummary, ScenarioDetail, ScenarioRow, Validate } from "./answers";
import type { CheckResult } from "./checkModel";
import { EMPTY_ECONOMICS, EMPTY_SCENARIO, type EconomicsForm, type FormIssue, type ScenarioForm } from "./forms";
import { EMPTY_CALENDAR, type CalendarForm } from "./formsCalendar";
import { EMPTY_ROCKS, type RocksForm } from "./formsRocks";
import { EMPTY_SLOPES, type SlopesForm } from "./formsSlopes";

export const PIT_SCENARIO_WINDOW = "pit-scenario";
export const PIT_ECONOMICS_WINDOW = "pit-economics";
export const SCENARIO_TITLE = "Сценарий карьера";
export const ECONOMICS_TITLE = "Экономика карьера";
export const PIT_ROCKS_WINDOW = "pit-rocks";
export const PIT_SLOPES_WINDOW = "pit-slopes";
export const PIT_CALENDAR_WINDOW = "pit-calendar";
export const PIT_CHECK_WINDOW = "pit-check";
export const ROCKS_TITLE = "Породы карьера";
export const SLOPES_TITLE = "Углы бортов карьера";
export const CALENDAR_TITLE = "Календарь карьера";
export const CHECK_TITLE = "Проверка шаблона карьера";

/** The windows that keep a form read from a scenario. */
export type FormKind = "econ" | "rocks" | "slopes" | "calendar";

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

/** The check of the template (`pit.check`) the window «Проверка шаблона» shows; `seq` — a check that was overtaken does not write its end. */
export interface PitCheck {
  phase: "idle" | "running" | "done" | "failed";
  seq: number;
  scenarioId: number | null;
  scenarioName: string;
  result: CheckResult | null;
  error: Refusal | null;
}

export const IDLE_CHECK: PitCheck = { phase: "idle", seq: 0, scenarioId: null, scenarioName: "", result: null, error: null };

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
  /** `validate` by the number of the scenario: «Запустить» of the one the commands work on does not depend on which one the windows show */
  validations: Record<number, Validate>;
  /** why the scenario `id` could not be read (a lost network, a model that is gone): the Russian text of the core; null — no error */
  loadError: { id: number; message: string } | null;
  /** the scenario each window's form was read from: «Применить» of a window writes there, never into another scenario that was chosen after */
  formFor: Partial<Record<FormKind, number>>;
  scenarioForm: ScenarioForm;
  scenarioIssue: FormIssue | null;
  econForm: EconomicsForm;
  econIssue: FormIssue | null;
  rocksForm: RocksForm;
  rocksIssue: FormIssue | null;
  slopesForm: SlopesForm;
  slopesIssue: FormIssue | null;
  calendarForm: CalendarForm;
  calendarIssue: FormIssue | null;
  check: PitCheck;
  run: PitRun;
}

const EMPTY: PitState = { defaults: null, models: {}, scenarios: [], scenariosLoaded: false, currentId: null, scenario: null, validate: null, validations: {}, loadError: null, formFor: {}, scenarioForm: EMPTY_SCENARIO, scenarioIssue: null, econForm: EMPTY_ECONOMICS, econIssue: null, rocksForm: EMPTY_ROCKS, rocksIssue: null, slopesForm: EMPTY_SLOPES, slopesIssue: null, calendarForm: EMPTY_CALENDAR, calendarIssue: null, check: IDLE_CHECK, run: IDLE };

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

export function setRocksForm(patch: Partial<RocksForm> | ((f: RocksForm) => RocksForm)): void {
  pitStore.set((s) => ({ rocksForm: typeof patch === "function" ? patch(s.rocksForm) : { ...s.rocksForm, ...patch }, rocksIssue: null }));
}

export function setSlopesForm(patch: Partial<SlopesForm> | ((f: SlopesForm) => SlopesForm)): void {
  pitStore.set((s) => ({ slopesForm: typeof patch === "function" ? patch(s.slopesForm) : { ...s.slopesForm, ...patch }, slopesIssue: null }));
}

export function setCalendarForm(patch: Partial<CalendarForm>): void {
  pitStore.set((s) => ({ calendarForm: { ...s.calendarForm, ...patch }, calendarIssue: null }));
}

export const setRocksIssue = (i: FormIssue | null): void => pitStore.set({ rocksIssue: i });
export const setSlopesIssue = (i: FormIssue | null): void => pitStore.set({ slopesIssue: i });
export const setCalendarIssue = (i: FormIssue | null): void => pitStore.set({ calendarIssue: i });

export const setScenarioIssue = (i: FormIssue | null): void => pitStore.set({ scenarioIssue: i });
export const setEconIssue = (i: FormIssue | null): void => pitStore.set({ econIssue: i });

/** Another project: clean windows and lists (the defaults are the core's, they stay). */
export function resetPit(): void {
  detailSeq++;
  listSeq++;
  pitStore.set((s) => ({ ...EMPTY, defaults: s.defaults, run: { ...IDLE, seq: s.run.seq + 1 }, check: { ...IDLE_CHECK, seq: s.check.seq + 1 } }));
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

// ---- the check of the template ----

/** A check begins: the old result goes (a result of another scenario must not stand beside the new one). */
export function beginCheck(scenarioId: number, scenarioName: string): number {
  const seq = pitStore.get().check.seq + 1;
  pitStore.set({ check: { phase: "running", seq, scenarioId, scenarioName, result: null, error: null } });
  return seq;
}

/** Writes into the check `seq` (a newer check is not touched). */
export function patchCheck(seq: number, patch: Partial<PitCheck>): boolean {
  if (pitStore.get().check.seq !== seq) return false;
  pitStore.set((s) => ({ check: { ...s.check, ...patch } }));
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

/** The words of a refusal of the core for a line in a window: the message and the hint. */
const refusalText = (r: Refusal): string => `${r.message} ${r.hint}`.trim();

/** Does the window of `kind` take a form that came with a command for the scenario `id`? Only the form of that scenario (or one that has none yet): another scenario's data does not enter it. */
export const formTakes = (kind: FormKind, id: number): boolean => {
  const from = pitStore.get().formFor[kind];
  return from === undefined || from === id;
};

/** Marks the scenario `id` as the one the windows' forms were read from (the windows of the kind write there only). */
export const setFormFor = (kind: FormKind, id: number): void => pitStore.set((s) => ({ formFor: { ...s.formFor, [kind]: id } }));

/**
 * Why the form of a window cannot be applied now, or null: it was read from one scenario and another is chosen since — its data would overwrite the other scenario.
 * A window that was never opened for a scenario (`formFor` empty) has nothing to compare.
 */
export function staleFormReason(s: PitState, kind: FormKind): string | null {
  const from = s.formFor[kind];
  if (from === undefined || s.scenario === null || s.scenario.id === from) return null;
  const was = s.scenarios.find((r) => r.id === from)?.name ?? `№${from}`;
  return `Форма прочитана из сценария «${was}», а выбран «${s.scenario.name}». Откройте окно командой ещё раз — форма прочитается из выбранного сценария.`;
}

/** The scenario `id` and its readiness (`validate` of its params); null — none (the windows then ask to create or to choose one). */
export async function selectScenario(id: number | null): Promise<ScenarioDetail | null> {
  const mine = ++detailSeq;
  const uid = projectUid();
  const fresh = () => mine === detailSeq && uid === projectUid();
  if (id === null) {
    pitStore.set({ currentId: null, scenario: null, validate: null, loadError: null });
    return null;
  }
  pitStore.set((s) => ({ currentId: id, loadError: null, scenario: s.scenario?.id === id ? s.scenario : null, validate: s.scenario?.id === id ? s.validate : null }));
  let detail: ScenarioDetail;
  try {
    detail = await loadScenario(id);
  } catch (e) {
    if (!fresh()) return null;
    const r = refusalOf(e);
    // a scenario that is gone (deleted in another window) leaves the state; any other failure is told, the scenario read before stays
    if (r.code === "pit_scenario_not_found") pitStore.set({ currentId: null, scenario: null, validate: null, loadError: null });
    else pitStore.set({ loadError: { id, message: refusalText(r) } });
    return null;
  }
  if (!fresh()) return null;
  pitStore.set({ scenario: detail });
  try {
    const v = await validateParams(detail.params);
    if (fresh()) pitStore.set((s) => ({ validate: v, validations: { ...s.validations, [id]: v } }));
  } catch (e) {
    // the scenario is read, its readiness is not (the model of the scenario is gone, the core is out of reach): the windows and «Запустить» say why
    if (fresh()) pitStore.set((s) => ({ loadError: { id, message: refusalText(refusalOf(e)) }, validate: null, validations: dropKey(s.validations, id) }));
    return detail;
  }
  return detail;
}

function dropKey<T>(m: Record<number, T>, id: number): Record<number, T> {
  const rest = { ...m };
  delete rest[id];
  return rest;
}

/** The readiness of the scenario `id` is not known any more (it changed elsewhere): «Запустить» waits for the next read. */
export const forgetValidation = (id: number | null): void => pitStore.set((s) => ({ validations: id === null ? {} : dropKey(s.validations, id) }));

/**
 * Reads the readiness of the scenario `id` without making it the one the windows show (a command of another scenario, the tree selection that is not the current):
 * `validations[id]` only. A failure is told to `loadError` of that scenario.
 */
export async function refreshValidation(id: number): Promise<void> {
  const uid = projectUid();
  try {
    const detail = await loadScenario(id);
    const v = await validateParams(detail.params);
    if (uid === projectUid()) pitStore.set((s) => ({ validations: { ...s.validations, [id]: v } }));
  } catch (e) {
    if (uid === projectUid()) pitStore.set((s) => ({ loadError: { id, message: refusalText(refusalOf(e)) }, validations: dropKey(s.validations, id) }));
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

/** Why `pit.run` cannot run now, or null: no scenario, the scenario is not read yet (or why it cannot be), a missing parameter (the first `missing`), a run goes. */
export function runReason(app: App): string | null {
  const id = currentScenarioId(app);
  if (id === null) return "Выберите или создайте сценарий карьера";
  if (app.store.get().projectReadOnly) return "Проект открыт только для чтения";
  if (isRunning()) return "Сценарий уже считается";
  const s = pitStore.get();
  if (s.loadError?.id === id) return s.loadError.message;
  const v = s.validations[id];
  if (!v) return "Сценарий загружается: подождите секунду";
  if ((s.scenario?.id === id ? s.scenario.state : scenarioById(id)?.state) === "running") return "Сценарий уже считается";
  if (!v.ready) return v.missing[0]?.message ?? "Сценарий не готов к расчёту";
  return null;
}
