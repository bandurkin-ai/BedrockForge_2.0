/**
 * What the window «Сравнить отчёты» knows (ТЗ 7.35 Д-37; ADR 0027 решение 26): the two reports chosen, the answer of the core (`POST /resources/compare`,
 * synchronous), the first refusal, the element and the quantity the table and the chart show, «только различия». One window per project; another
 * project is a clean window (the window asks it when it opens). A read overtaken by a newer one is dropped. No React.
 */
import { CoreError } from "../../shell/api";
import { Store } from "../../shell/store";
import { projectStore } from "../projects/state";
import type { CompareAnswer } from "./answers";
import { compareReports, refusalOf, type Refusal } from "./api";
import type { Metric } from "./compareModel";

export const COMPARE_WINDOW = "resources-compare";
export const COMPARE_TITLE = "Сравнить отчёты";

export type ComparePhase = "idle" | "loading" | "done" | "failed";

export interface CompareState {
  a: number | null;
  b: number | null;
  phase: ComparePhase;
  answer: CompareAnswer | null;
  error: Refusal | null;
  seq: number;
  element: string | null;
  metric: Metric;
  onlyDiff: boolean;
  uid: string | null;
}

const EMPTY: CompareState = { a: null, b: null, phase: "idle", answer: null, error: null, seq: 0, element: null, metric: "tonnes", onlyDiff: false, uid: null };

export const compareStore = new Store<CompareState>(EMPTY);

/** A clean window of the project that is open now. */
export function resetCompare(): void {
  compareStore.set((s) => ({ ...EMPTY, seq: s.seq + 1, uid: projectStore.get().current?.uid ?? null }));
}

/** The window of another project is dropped. */
export function syncCompare(): void {
  if ((projectStore.get().current?.uid ?? null) !== compareStore.get().uid) resetCompare();
}

/** The report A or B chosen: the old answer goes (it is the answer for another pair). */
export function chooseReport(side: "a" | "b", id: number | null): void {
  compareStore.set((s) => ({ ...s, [side]: id, phase: "idle", answer: null, error: null, seq: s.seq + 1 }));
}

export const setElement = (element: string | null): void => compareStore.set({ element });
export const setMetric = (metric: Metric): void => compareStore.set({ metric });
export const setOnlyDiff = (onlyDiff: boolean): void => compareStore.set({ onlyDiff });

/** Reads the comparison of the pair and puts it in the window; a refusal stands in the window and is thrown (the command tells it in words). */
export async function loadCompare(a: number, b: number): Promise<CompareAnswer> {
  const seq = compareStore.get().seq + 1;
  compareStore.set({ a, b, phase: "loading", answer: null, error: null, seq });
  try {
    const answer = await compareReports(a, b);
    if (compareStore.get().seq === seq) compareStore.set({ phase: "done", answer, error: null });
    return answer;
  } catch (e) {
    const error = refusalOf(e);
    if (compareStore.get().seq === seq) compareStore.set({ phase: "failed", answer: null, error });
    throw e instanceof CoreError ? e : new CoreError(0, error.code, error.message, error.hint);
  }
}
