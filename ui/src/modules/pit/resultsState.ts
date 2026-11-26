/**
 * What the windows «Поверхности оболочек», «Каркас карьера» and «Сравнить сценарии карьера» know (ТЗ 8.11.5; ADR 0028 решения 25–27, 29): the shells picked for the
 * surfaces, the shell and the name of the solid, the course and the result of the two jobs, the two scenarios of the comparison and its answer. A job that was
 * overtaken (another one began, the project changed) does not write its end (`seq`). Another project is a clean state. The scenario the windows work on is
 * `pitStore` (`currentScenarioId`). No React.
 */
import { CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import { refusalOf, type Refusal, type Started } from "./api";
import type { CompareAnswer, ShellKey, SolidResult, SurfacesResult } from "./resultsAnswers";
import { compareScenarios } from "./resultsApi";

export const PIT_SURFACES_WINDOW = "pit-surfaces";
export const PIT_SOLID_WINDOW = "pit-solid";
export const PIT_COMPARE_WINDOW = "pit-compare";
export const SURFACES_TITLE = "Поверхности оболочек карьера";
export const SOLID_TITLE = "Каркас карьера";
export const COMPARE_TITLE = "Сравнить сценарии карьера";

export type JobPhase = "idle" | "running" | "done" | "failed" | "cancelled";

/** The course of a quick job: the scenario it works on, the job (its progress is in the list of the jobs), the result, the first refusal. */
export interface JobRun<R> {
  phase: JobPhase;
  seq: number;
  scenarioId: number | null;
  scenarioName: string;
  job: Started | null;
  result: R | null;
  error: Refusal | null;
}

const idleRun = <R>(): JobRun<R> => ({ phase: "idle", seq: 0, scenarioId: null, scenarioName: "", job: null, result: null, error: null });

export type ComparePhase = "idle" | "loading" | "done" | "failed";

export interface PitCompareState {
  a: number | null;
  b: number | null;
  phase: ComparePhase;
  answer: CompareAnswer | null;
  error: Refusal | null;
  seq: number;
  /** the quantity of the table and of the chart */
  metric: ShellKey;
}

export interface ResultsState {
  /** the shells picked for the surfaces — of the scenario `scenarioId`; null — all the shells of the scenario */
  picked: { scenarioId: number; shells: number[] } | null;
  surfaces: JobRun<SurfacesResult>;
  /** the shell and the name of the solid (empty name — the core names it) */
  solidShell: { scenarioId: number; shell: number } | null;
  solidName: string;
  solid: JobRun<SolidResult>;
  compare: PitCompareState;
}

const EMPTY_COMPARE: PitCompareState = { a: null, b: null, phase: "idle", answer: null, error: null, seq: 0, metric: "npv_best" };

const EMPTY: ResultsState = { picked: null, surfaces: idleRun(), solidShell: null, solidName: "", solid: idleRun(), compare: EMPTY_COMPARE };

export const resultsStore = new Store<ResultsState>(EMPTY);

/** Another project: clean windows (the overtaken jobs and reads are dropped by `seq`). */
export function resetResults(): void {
  resultsStore.set((s) => ({ ...EMPTY, surfaces: { ...idleRun<SurfacesResult>(), seq: s.surfaces.seq + 1 }, solid: { ...idleRun<SolidResult>(), seq: s.solid.seq + 1 }, compare: { ...EMPTY_COMPARE, seq: s.compare.seq + 1 } }));
}

export const isBusy = (kind: "surfaces" | "solid"): boolean => resultsStore.get()[kind].phase === "running";

/** A job begins: the old result goes (the result of another scenario must not stand beside the new one). */
export function beginJob(kind: "surfaces" | "solid", scenarioId: number, scenarioName: string): number {
  const seq = resultsStore.get()[kind].seq + 1;
  resultsStore.set({ [kind]: { phase: "running", seq, scenarioId, scenarioName, job: null, result: null, error: null } } as Partial<ResultsState>);
  return seq;
}

/** Writes into the job `seq` of a kind; a newer job is not touched. */
export function patchJob<K extends "surfaces" | "solid">(kind: K, seq: number, patch: Partial<ResultsState[K]>): boolean {
  if (resultsStore.get()[kind].seq !== seq) return false;
  resultsStore.set((s) => ({ [kind]: { ...s[kind], ...patch } }) as Partial<ResultsState>);
  return true;
}

// ---- the choices of the windows ----

/** The shells picked for the surfaces (of a scenario); `shells` null — all of them again. */
export function pickShells(scenarioId: number, shells: number[] | null): void {
  resultsStore.set({ picked: shells === null ? null : { scenarioId, shells } });
}

/** The picked shells of a scenario (another scenario's choice is not this one's); null — all. */
export const pickedFor = (scenarioId: number | null): number[] | null => {
  const p = resultsStore.get().picked;
  return p && p.scenarioId === scenarioId ? p.shells : null;
};

export function chooseSolidShell(scenarioId: number, shell: number | null): void {
  resultsStore.set({ solidShell: shell === null ? null : { scenarioId, shell } });
}

export const setSolidName = (solidName: string): void => resultsStore.set({ solidName });

/** The shell of the solid chosen for a scenario, else null. */
export const solidShellFor = (scenarioId: number | null): number | null => {
  const c = resultsStore.get().solidShell;
  return c && c.scenarioId === scenarioId ? c.shell : null;
};

/** A scenario A or B chosen: the old answer goes (it is the answer for another pair). */
export function chooseCompare(side: "a" | "b", id: number | null): void {
  resultsStore.set((s) => ({ compare: { ...s.compare, [side]: id, phase: "idle", answer: null, error: null, seq: s.compare.seq + 1 } }));
}

export const setCompareMetric = (metric: ShellKey): void => resultsStore.set((s) => ({ compare: { ...s.compare, metric } }));

/** Reads the comparison of the pair and puts it in the window; a refusal stands in the window and is thrown (the command tells it in words). */
export async function loadCompare(a: number, b: number): Promise<CompareAnswer> {
  const seq = resultsStore.get().compare.seq + 1;
  const uid = projectStore.get().current?.uid ?? null;
  const live = () => resultsStore.get().compare.seq === seq && (projectStore.get().current?.uid ?? null) === uid;
  resultsStore.set((s) => ({ compare: { ...s.compare, a, b, phase: "loading", answer: null, error: null, seq } }));
  try {
    const answer = await compareScenarios(a, b);
    if (live()) resultsStore.set((s) => ({ compare: { ...s.compare, phase: "done", answer, error: null } }));
    return answer;
  } catch (e) {
    const error = refusalOf(e);
    if (live()) resultsStore.set((s) => ({ compare: { ...s.compare, phase: "failed", answer: null, error } }));
    throw e instanceof CoreError ? e : new CoreError(0, error.code, error.message, error.hint);
  }
}
