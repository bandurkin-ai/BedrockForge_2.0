/**
 * The run of the simulation as the windows see it (ТЗ 8.8.1; docs/progress/e18-api.md §9): the job `simulate.run` followed from the
 * start to its end, and the journal of its result — the nodes and the realizations, the singular systems, the nodes with no datum,
 * the conditioning samples of a node, ρ of the co-SGS, the lines of the log of the core. The command `simulate.run` (`bind.ts`) writes
 * here whoever called it (the wizard, the tree, the ribbon, the keys, the assistant); the wizard shows it. The progress and the stage
 * of the job («Нормальные оценки», «Реализации k / N», «Запись») are in the list of «Задания» (`jobsStore`). One run at a time is shown.
 * No React.
 */
import { Store } from "../../shell/store";
import type { Refusal, RunResult, Started } from "./api";

export type RunPhase = "idle" | "running" | "done" | "failed" | "cancelled";

export interface SimRunState {
  phase: RunPhase;
  /** the job of the core, once it is started */
  job: Started | null;
  /** the row run; null while a run of a form not saved yet has no number */
  simId: number | null;
  name: string;
  result: RunResult | null;
  /** the line the command answers with */
  summary: string | null;
  error: Refusal | null;
  /** a cancelled run: a line, not an error */
  note: string | null;
}

export const IDLE_RUN: SimRunState = { phase: "idle", job: null, simId: null, name: "", result: null, summary: null, error: null, note: null };

export const runStore = new Store<SimRunState>(IDLE_RUN);

export const runBegin = (simId: number | null, name: string): void => runStore.set({ ...IDLE_RUN, phase: "running", simId, name });
export const runJob = (job: Started): void => runStore.set({ job });
export const runDone = (result: RunResult, summary: string): void => runStore.set({ phase: "done", simId: result.simulation_id, result, summary, error: null, note: null });
export const runCancelled = (note: string): void => runStore.set({ phase: "cancelled", note });
export const runFailed = (error: Refusal): void => runStore.set({ phase: "failed", error });
export const runReset = (): void => runStore.set(IDLE_RUN);

export const runBusy = (): boolean => runStore.get().phase === "running";

const nf = (n: number): string => n.toLocaleString("ru-RU");
const g = (x: number | null, digits = 4): string => (x === null ? "—" : x.toLocaleString("ru-RU", { maximumSignificantDigits: digits }));

/** The lines under the result: the counts of the nodes, the conditioning samples, ρ, the log of the core. */
export function journalLines(r: RunResult): string[] {
  const out: string[] = [];
  const c = r.counts;
  const parts = [
    c.degenerate ? `вырожденных систем ${nf(c.degenerate)}` : "",
    c.free ? `узлов без проб в окрестности ${nf(c.free)}` : "",
    c.on_sample ? `узлов на пробах ${nf(c.on_sample)}` : "",
    c.gap ? `узлов без вторичной переменной ${nf(c.gap)} (смоделированы как SGS)` : "",
  ].filter(Boolean);
  const unknown = c.degenerate === null && c.free === null && c.on_sample === null;
  out.push(parts.length ? `Узлы: ${parts.join(", ")}.` : unknown ? "Узлы: ядро не сообщило число вырожденных систем и узлов без проб." : "Узлы: вырожденных систем и узлов без проб нет.");
  if (r.cond) out.push(`Условных данных в узле: наименьшее ${g(r.cond.min)}, среднее ${g(r.cond.mean)}, наибольшее ${g(r.cond.max)}.`);
  if (r.secondary) {
    const s = r.secondary;
    out.push(`Вторичная переменная «${s.field}»: ρ = ${g(s.rho_used ?? s.rho)}${s.pairs ? ` (по ${nf(s.pairs)} парам проба–узел)` : " (задан)"}.`);
  }
  return [...out, ...r.log];
}

/** «Смоделировано: 200 узлов × 5 реализаций за 8,9 с (моделирование «AU · SGS · окисл»).» */
export function runSummary(name: string, r: RunResult): string {
  const sec = r.seconds === null ? "" : ` за ${g(r.seconds, 3)} с`;
  return `Моделирование «${name}» выполнено: ${nf(r.nodes)} узлов × ${nf(r.realizations)} реализаций${sec}.`;
}
