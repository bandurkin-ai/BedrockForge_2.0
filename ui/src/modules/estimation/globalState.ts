/**
 * The global check of an estimate and its Q-Q (ТЗ 8.8.5 «Глобальная статистика», «Гистограммы и Q-Q»; docs/progress/e18-api.md §8.3–§8.4):
 * what the window «Глобальная проверка оценки» and the commands `estimate.global`, `estimate.qq` share — the row, the job `estimate.global`
 * (the means of the composites, of the nearest sample and of the estimate on the same blocks; the norm is 5 %), the Q-Q of the composites
 * against the blocks (at once), the words. The norm is told in words and colour (the brand's green and red), never in colour only.
 * One check at a time. No React.
 */
import { refusal } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import { cancelJob } from "../jobs/state";
import { refusalOf, type EstimationRow, type Refusal, type Started } from "./api";
import { loadQq, readGlobal, startGlobal, type BlockMoments, type GlobalResult, type Moments, type QqAnswer } from "./checksApi";
import { followJob } from "./checksRun";
import { num } from "./knaState";
import { estimationStore } from "./state";

export const GLOBAL_WINDOW = "estimation-global";
export const GLOBAL_TITLE = "Глобальная проверка оценки";

export type CheckPhase = "idle" | "running" | "done" | "failed" | "cancelled";

export interface GlobalState {
  rowId: number | null;
  phase: CheckPhase;
  job: Started | null;
  result: GlobalResult | null;
  error: Refusal | null;
  note: string | null;
  /** Q-Q: the samples by the declustering weights, the blocks by the volume */
  weighted: boolean;
  qqPhase: "idle" | "loading" | "done" | "failed";
  qq: QqAnswer | null;
  qqError: Refusal | null;
}

const fresh = (): GlobalState => ({ rowId: null, phase: "idle", job: null, result: null, error: null, note: null, weighted: true, qqPhase: "idle", qq: null, qqError: null });

export const globalStore = new Store<GlobalState>(fresh());

export const resetGlobal = (): void => globalStore.set(fresh());

const drop = { phase: "idle", job: null, result: null, error: null, note: null, qqPhase: "idle", qq: null, qqError: null } as const;

export function setGlobalRow(id: number | null): void {
  if (globalStore.get().phase === "running") return;
  seqQq++; // a Q-Q of the old row is not shown
  globalStore.set({ rowId: id, ...drop });
}

/** The rows of the project: the others have no fields in the model yet (the core says `not_estimated` for them). */
export const checkedRows = (rows: readonly EstimationRow[] = estimationStore.get().rows): EstimationRow[] => rows.filter((r) => r.last_run !== null);

/** Why the check cannot be started now (no row, not estimated yet, busy); null — it can. */
export function globalReason(s: Pick<GlobalState, "rowId" | "phase">): string | null {
  if (s.phase === "running") return "Проверка уже выполняется";
  if (s.rowId === null) return "Выберите строку «Параметры оценки»";
  const row = estimationStore.get().rows.find((r) => r.id === s.rowId);
  if (!row) return estimationStore.get().loaded ? `Строка №${s.rowId} не найдена` : null;
  if (row.source_lost) return "Источник параметров оценки удалён";
  if (!row.last_run) return "Строка ещё не выполнялась: сначала «Выполнить оценку»";
  return null;
}

const refuse = (s: Pick<GlobalState, "rowId" | "phase">): void => {
  const why = globalReason(s);
  if (why) throw refusal(s.phase === "running" ? "busy" : "bad_param", why, s.phase === "running" ? "Дождитесь конца или отмените проверку." : "Выберите выполненную строку «Параметры оценки».");
};

// ---- the run ----

let seq = 0;
let seqQq = 0;

/** The job `estimate.global`; then the Q-Q. The answer is the words about the norm. */
export async function runGlobal(): Promise<string> {
  const s = globalStore.get();
  refuse(s);
  const id = s.rowId!;
  const mine = ++seq;
  const live = () => mine === seq;
  globalStore.set({ ...drop, phase: "running" });
  try {
    const started = await startGlobal(id);
    if (live()) globalStore.set({ job: started });
    const end = await followJob(started, { message: "Глобальная проверка не выполнена.", hint: "Повторите проверку." });
    if (end.kind === "stale") {
      if (live()) resetGlobal();
      return "Проект изменился: результат больше не показывается";
    }
    if (end.kind === "cancelled") {
      const note = "Глобальная проверка отменена: ничего не записано";
      if (live()) globalStore.set({ phase: "cancelled", job: null, note });
      return note;
    }
    const result = readGlobal(end.result);
    if (!result) throw refusal("bad_answer", "Ядро вернуло проверку без итогов.", "Повторите проверку; если повторится — обновите программу.");
    if (live()) globalStore.set({ phase: "done", job: null, result });
    const qq = live() ? await checkQq(id).catch(() => null) : null; // the Q-Q has its own words under it
    return globalSummary(result) + (qq ? ` ${qqSummary(qq)}` : "");
  } catch (e) {
    const r = refusalOf(e);
    if (live()) globalStore.set({ phase: "failed", job: null, error: r });
    throw refusal(r.code, r.message, r.hint);
  }
}

export async function cancelGlobal(): Promise<void> {
  const job = globalStore.get().job;
  if (job) await cancelJob(job);
}

/** The Q-Q of the row on the screen (at once, a heavy slot of the core); the newest answer only. */
export async function checkQq(id: number): Promise<QqAnswer> {
  const mine = ++seqQq;
  globalStore.set({ qqPhase: "loading", qqError: null });
  try {
    const qq = await loadQq(id, globalStore.get().weighted);
    if (mine === seqQq) globalStore.set({ qqPhase: "done", qq });
    return qq;
  } catch (e) {
    const r = refusalOf(e);
    if (mine === seqQq) globalStore.set({ qqPhase: "failed", qq: null, qqError: r });
    throw refusal(r.code, r.message, r.hint);
  }
}

/** `estimate.qq`: the Q-Q alone (without the job of the global check). */
export async function runQq(): Promise<string> {
  const s = globalStore.get();
  if (s.rowId === null) throw refusal("bad_param", "Не выбрана строка оценки.", "Выберите выполненную строку «Параметры оценки».");
  return qqSummary(await checkQq(s.rowId));
}

/** The switch «С весами»: the Q-Q is asked again. */
export function setWeighted(weighted: boolean): void {
  globalStore.set({ weighted });
  const id = globalStore.get().rowId;
  if (id !== null && globalStore.get().qq) void checkQq(id).catch(() => undefined); // a refusal stands under the chart
}

// ---- the words ----

/** «+6,24 %», «−1,71 %» (the sign is always told, the minus is the typographic one whatever the locale of the system says). */
const pct = (x: number | null): string => (x === null ? "—" : `${x < 0 ? "−" : x > 0 ? "+" : ""}${Math.abs(x).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} %`);

export type Verdict = "ok" | "bad" | "none";

/** The norm of a difference: green («в норме») within the limit, red («вне нормы») beyond it, none — it cannot be told. */
export function verdictOf(diff: number | null, norm: boolean | null): Verdict {
  if (diff === null || norm === null) return "none";
  return norm ? "ok" : "bad";
}

export function verdictText(diff: number | null, norm: boolean | null, limit: number): string {
  const v = verdictOf(diff, norm);
  if (v === "none") return "не определено";
  return `${pct(diff)} — ${v === "ok" ? "в норме" : "вне нормы"} (допуск ±${limit.toLocaleString("ru-RU")} %)`;
}

/** What the estimate is compared with: the declustered mean, or the naive one when the row has no weights. */
export const referenceText = (g: Pick<GlobalResult, "composites">): string => (g.composites.reference === "declus" ? "декластерированного среднего проб" : "среднего проб (весов декластеризации нет)");

export interface StatRow {
  key: "naive" | "declus" | "nn" | "estimate";
  title: string;
  n: number;
  /** the volume of the blocks, m³ */
  volume: number | null;
  mean: number | null;
  var: number | null;
  cv: number | null;
}

/** The table of the summary: the composites (naive, declustered) and the blocks (the nearest sample, the estimate). */
export function statRows(g: GlobalResult): StatRow[] {
  const comp = (key: "naive" | "declus", title: string, m: Moments | null): StatRow[] => (m ? [{ key, title, n: m.n, volume: null, mean: m.mean, var: m.var, cv: m.cv }] : []);
  const blocks = (key: "nn" | "estimate", title: string, m: BlockMoments | null): StatRow[] => (m ? [{ key, title, n: m.n, volume: m.volume, mean: m.mean, var: m.var, cv: m.cv }] : []);
  return [...comp("naive", "Пробы домена, без весов", g.composites.naive), ...comp("declus", "Пробы домена, декластеризация", g.composites.declus), ...blocks("nn", "Блоки, ближайший сосед (NN)", g.nn), ...blocks("estimate", "Блоки, оценка", g.estimate)];
}

/** The variance of the blocks against the expected one (C̄_VV of the model for the size of the block) in words. */
export function varianceText(g: Pick<GlobalResult, "block_var">): string {
  const v = g.block_var;
  if (v.estimate === null) return "Дисперсия блоков не посчитана.";
  if (v.expected === null) return `Дисперсия оценки блоков ${num(v.estimate, 4)}; ожидаемая дисперсия не считается: у метода нет вариограммы в исходных единицах.`;
  const ratio = v.ratio === null ? "" : `, отношение ${num(v.ratio, 2)}`;
  const sill = v.sill === null ? "" : `; порог вариограммы ${num(v.sill, 4)}`;
  return `Дисперсия оценки блоков ${num(v.estimate, 4)}, ожидаемая дисперсия блоков C̄_VV ${num(v.expected, 4)}${ratio}${sill}.`;
}

/** What the ratio of the variances says: kriging smooths, so the estimate varies less than the real blocks; above 1 is not expected. */
export function ratioHint(g: Pick<GlobalResult, "block_var">): string | null {
  const r = g.block_var.ratio;
  if (r === null) return null;
  if (r > 1) return "Дисперсия оценки больше ожидаемой для блоков такого размера: проверьте вариограмму и размер блока.";
  if (r < 0.1) return "Оценка сильно сглажена относительно ожидаемой дисперсии блоков: проверьте радиусы поиска и число проб.";
  return "Кригинг сглаживает: дисперсия оценки ниже ожидаемой дисперсии блоков — это нормально.";
}

/** «Оценено 400 из 800 блоков: вне домена 400» — the blocks the sums stand on. */
export function blocksText(g: Pick<GlobalResult, "blocks">): string {
  const b = g.blocks;
  const nf = (n: number) => n.toLocaleString("ru-RU");
  const words = ["оценён", "вне домена", "мало проб", "вырожденная система"].flatMap((w, i) => (b.by_status[i] ? [`${w} ${nf(b.by_status[i])}`] : []));
  const lost = [b.empty ? `без значения ${nf(b.empty)}` : "", b.no_volume ? `без объёма ${nf(b.no_volume)}` : ""].filter(Boolean);
  return `В сумме ${nf(b.used)} из ${nf(b.rows)} блоков${words.length ? `: ${words.join(", ")}` : ""}${lost.length ? `; ${lost.join(", ")}` : ""}.`;
}

export function globalSummary(g: GlobalResult): string {
  return `Глобальная проверка «${g.name}» (${g.field}): среднее оценки ${num(g.estimate?.mean ?? null, 4)} против ${referenceText(g)} ${num((g.composites[g.composites.reference] ?? null)?.mean ?? null, 4)}: ${verdictText(g.diff_pct, g.norm, g.limit_pct)}. ${varianceText(g)}${g.warnings.length ? ` ${g.warnings.join(" ")}` : ""}`;
}

/** The Q-Q in words: the ratio of the quantiles and the distance of the two distributions. */
export function qqSummary(q: QqAnswer): string {
  const r = q.ratio.filter((x): x is number => x !== null);
  const range = r.length ? `отношение Y/X по квантилям от ${num(Math.min(...r), 2)} до ${num(Math.max(...r), 2)}` : "отношение квантилей не считается";
  return `Q-Q: композиты (${q.weighted ? "с весами декластеризации" : "без весов"}) против оценки блоков — ${range}${q.ks === null ? "" : `, расстояние Колмогорова–Смирнова ${num(q.ks, 3)}`}.`;
}
