/**
 * The state of the tab «Статистика» outside React (ADR 0019 §7): the analysis, the set (null — the active one),
 * the table and the component (null — the first ones), the forms, the last calculation of each analysis. One
 * calculation of an analysis at a time: a newer start drops the answer of an older one; a change of the set, the
 * table, the component or of the data (`drillholes.changed`) drops the results — they are not stored (7.29) and
 * are computed again on request. No React.
 */
import { refusal } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import { loadFields } from "../charts/api";
import { tablesStore } from "../tables/state";
import { tabBacktr, tabBootstrap, tabDeclus, tabLocal, tabMap, tabNscore, tabVariogram, tabVariogramModel, type BacktrAnswer, type BootstrapAnswer, type DeclusAnswer, type NscoreAnswer, type TabLocalAnswer, type TabMapAnswer, type TabModelAnswer, type TabVariogramAnswer } from "./api";
import { backtrBody, bootstrapBody, declusBody, DEFAULT_FORMS, nscoreBody, pickContext, problemOf, type Built, type Problem, type StatsForms, type TabAnalysis, type TabContext } from "./forms";
import { DEFAULT_VARIO_FORMS, localBody, mapBody, modelBody, variogramBody, type VarioForms } from "./formsVario";

export interface Run<T> {
  busy: boolean;
  error: Problem | null;
  result: T | null;
}

export type BacktrRun = BacktrAnswer & { scores: number[] };

/** The experimental variogram (null — the curve of the model alone) and the curve of the model (null — none). */
export interface VariogramRun {
  data: TabVariogramAnswer | null;
  curve: TabModelAnswer | null;
}

export type AllForms = StatsForms & VarioForms;

export interface StatsRuns {
  declus: Run<DeclusAnswer>;
  nscore: Run<NscoreAnswer>;
  backtr: Run<BacktrRun>;
  bootstrap: Run<BootstrapAnswer>;
  variogram: Run<VariogramRun>;
  map: Run<TabMapAnswer>;
  local: Run<TabLocalAnswer>;
}

export type RunKind = keyof StatsRuns;

export interface StatsState {
  analysis: TabAnalysis;
  /** null — the active set */
  setId: number | null;
  /** `name_key`, name or id of the table; null — the first table of the set */
  table: string | null;
  /** null — the first component */
  component: string | null;
  /** bumped by an edit of the data: the tab reads the fields again */
  rev: number;
  forms: AllForms;
  runs: StatsRuns;
}

const IDLE: Run<never> = { busy: false, error: null, result: null };
const idleRuns = (): StatsRuns => ({ declus: IDLE, nscore: IDLE, backtr: IDLE, bootstrap: IDLE, variogram: IDLE, map: IDLE, local: IDLE });

export const ALL_DEFAULT_FORMS: AllForms = { ...DEFAULT_FORMS, ...DEFAULT_VARIO_FORMS };

export const initialStats = (): StatsState => ({ analysis: "declus", setId: null, table: null, component: null, rev: 0, forms: ALL_DEFAULT_FORMS, runs: idleRuns() });

export const statsStore = new Store<StatsState>(initialStats());

const tickets: Record<RunKind, number> = { declus: 0, nscore: 0, backtr: 0, bootstrap: 0, variogram: 0, map: 0, local: 0 };

/** Drops every result; answers still on their way are dropped too. */
export function clearRuns(): void {
  for (const k of Object.keys(tickets) as RunKind[]) tickets[k] += 1;
  statsStore.set({ runs: idleRuns() });
}

export interface ContextPatch {
  setId?: number | null;
  table?: string | null;
  component?: string | null;
}

/**
 * Sets the set, the table, the component: an absent key keeps its value, another set takes the first table and
 * its first component unless they are given, another table — its first component. A change drops the results.
 */
export function setContext(p: ContextPatch): void {
  const s = statsStore.get();
  const setId = p.setId !== undefined ? p.setId : s.setId;
  const setChanged = setId !== s.setId;
  const table = p.table !== undefined ? p.table : setChanged ? null : s.table;
  const component = p.component !== undefined ? p.component : setChanged || table !== s.table ? null : s.component;
  if (setId === s.setId && table === s.table && component === s.component) return;
  statsStore.set({ setId, table, component });
  clearRuns();
}

export const setAnalysis = (analysis: TabAnalysis): void => statsStore.set({ analysis });

export function setForm<K extends keyof AllForms>(key: K, patch: Partial<AllForms[K]>): void {
  statsStore.set((s) => ({ forms: { ...s.forms, [key]: { ...s.forms[key], ...patch } } }));
}

/** An edit of the data of a set (all sets without one): the results of the tab are no longer of these data. */
export function statsDataChanged(setId?: number): void {
  const s = statsStore.get();
  const mine = s.setId ?? tablesStore.get().setId;
  if (setId !== undefined && mine !== null && setId !== mine) return;
  statsStore.set({ rev: s.rev + 1 });
  clearRuns();
}

/** The set the tab computes on: the chosen one, else the active one. */
export function tabSetId(s: Pick<StatsState, "setId"> = statsStore.get()): number | null {
  return s.setId ?? tablesStore.get().setId;
}

/** The set, the table and the component of a calculation, as the tab shows them (the same rule: `pickContext`). */
export async function resolveContext(s: StatsState = statsStore.get()): Promise<TabContext> {
  const setId = tabSetId(s);
  if (setId === null) throw refusal("set_not_found", "Нет активного набора скважин.", "Выберите набор в дереве проекта или на вкладке «Статистика».");
  const f = await loadFields(setId, s.table);
  if (f.table === null) throw refusal("table_not_found", "В наборе нет таблиц проб.", "Импортируйте опробование или постройте композиты.");
  return pickContext(setId, f, s.component);
}

function setRun(kind: RunKind, run: Partial<Run<unknown>>): void {
  statsStore.set((s) => ({ runs: { ...s.runs, [kind]: { ...s.runs[kind], ...run } } }));
}

const checked = <T>(b: Built<T>): T => {
  if (b.reason !== undefined) throw refusal("bad_param", `${b.reason}.`, "Исправьте поле формы и повторите расчёт.");
  return b.body;
};

async function compute(kind: RunKind, s: StatsState, c: TabContext): Promise<StatsRuns[RunKind]["result"]> {
  const f = s.forms;
  if (kind === "declus") return tabDeclus(checked(declusBody(c, f.declus)));
  if (kind === "nscore") return tabNscore(checked(nscoreBody(c, f.nscore)));
  if (kind === "bootstrap") return tabBootstrap(checked(bootstrapBody(c, f.bootstrap)));
  if (kind === "map") return tabMap(checked(mapBody(c, f.map)));
  if (kind === "local") return tabLocal(checked(localBody(c, f.local)));
  if (kind === "variogram") {
    const v = f.variogram;
    const data = await tabVariogram(checked(variogramBody(c, v)));
    const curve = v.withModel ? await tabVariogramModel(checked(modelBody(v, data))) : null;
    return { data, curve };
  }
  const body = checked(backtrBody(c, f.nscore, f.backtr));
  return { ...(await tabBacktr(body)), scores: body.scores };
}

/**
 * Computes an analysis with the forms of the tab; the answer (or the refusal, shown with its hint) goes to its
 * run unless a newer start or a change of the context overtook it. Resolves with the answer; rejects with the
 * error (the command tells it to the assistant).
 */
export async function runAnalysis<K extends RunKind>(kind: K): Promise<NonNullable<StatsRuns[K]["result"]>> {
  const ticket = ++tickets[kind];
  const s = statsStore.get();
  setRun(kind, { busy: true, error: null });
  try {
    const c = await resolveContext(s);
    const result = (await compute(kind, s, c)) as NonNullable<StatsRuns[K]["result"]>;
    if (ticket === tickets[kind]) setRun(kind, { busy: false, error: null, result });
    return result;
  } catch (e) {
    if (ticket === tickets[kind]) setRun(kind, { busy: false, error: problemOf(e) });
    throw e;
  }
}

/**
 * The curve of the model of the form without data (ТЗ 7.29: «кривая модели — и без данных»): no open set is
 * needed; the points of an earlier calculation are dropped (they were not of this model).
 */
export async function runModelCurve(): Promise<TabModelAnswer> {
  const ticket = ++tickets.variogram;
  setRun("variogram", { busy: true, error: null });
  try {
    const curve = await tabVariogramModel(checked(modelBody(statsStore.get().forms.variogram, null)));
    if (ticket === tickets.variogram) setRun("variogram", { busy: false, error: null, result: { data: null, curve } });
    return curve;
  } catch (e) {
    if (ticket === tickets.variogram) setRun("variogram", { busy: false, error: problemOf(e) });
    throw e;
  }
}
