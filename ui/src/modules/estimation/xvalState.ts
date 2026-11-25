/**
 * The cross-validation of the open project as the window sees it (ТЗ 8.8.5; docs/progress/e18-api.md §8.1): the row chosen, the
 * result read — the whole table in pages of 5000 (the sort, the table pages and the scatter plot work over the rows read; the core
 * sorts nothing), the summary, the histogram — the job `estimate.xval` with its progress and cancel, the errors in 3D. A result
 * of the core is only read: the job writes it, `GET` gives it back (404 `xval_not_found` — none yet). `stale` — the parameters or
 * the inputs of the row are not those of the calculation: the window says so and why. A read overtaken by a newer one is dropped.
 * No React.
 */
import { CoreError } from "../../shell/api";
import { refusal } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import { loadJob } from "../blocks/build/api";
import { projectStore } from "../projects/state";
import { refusalOf, waitJob, type Refusal, type Started } from "./api";
import { estimationStore, rowById } from "./state";
import { loadXvalPage, NOT_FOUND, PAGE_MAX, readXvalResult, startXval, type XvalBy, type XvalHistogram, type XvalPage, type XvalRow, type XvalSummary } from "./xvalApi";
import { errorCloud, nextSort, resultLine, type ErrorCloud, type Sort, type SortKey } from "./xvalModel";
import { errorsShown, hideErrors, showErrors } from "./xvalScene";

export const XVAL_WINDOW = "estimation-xval";
export const XVAL_TITLE = "Перекрёстная проверка";
/** The rows read at most (20 requests of 5000): more is shown by the first of them, the window says so. */
export const ROWS_MAX = 100_000;

export type XvalTab = "table" | "scatter" | "hist";
export type XvalPhase = "idle" | "loading" | "ready" | "none" | "running" | "failed";

/** What the core says about the calculation besides the rows. */
export interface XvalMeta {
  by: XvalBy;
  at: string | null;
  stale: boolean;
  total: number;
  summary: XvalSummary;
  histogram: XvalHistogram;
}

export interface XvalState {
  estimationId: number | null;
  /** what the next run excludes */
  by: XvalBy;
  phase: XvalPhase;
  meta: XvalMeta | null;
  rows: XvalRow[];
  /** the rows of the table are not all read yet: the pages read, the pages in all */
  reading: { done: number; of: number } | null;
  job: Started | null;
  error: Refusal | null;
  note: string | null;
  sort: Sort | null;
  tablePage: number;
  size: number;
  tab: XvalTab;
  /** the errors are in the 3D window */
  shown: { n: number; limit: number } | null;
}

export const IDLE_XVAL: XvalState = { estimationId: null, by: "hole", phase: "idle", meta: null, rows: [], reading: null, job: null, error: null, note: null, sort: null, tablePage: 1, size: 100, tab: "table", shown: null };

export const xvalStore = new Store<XvalState>(IDLE_XVAL);

let seq = 0;

const metaOf = (p: XvalPage): XvalMeta => ({ by: p.by, at: p.at, stale: p.stale, total: p.total, summary: p.summary, histogram: p.histogram });

/** The rows of the table are fewer than the core has (the cap `ROWS_MAX`). */
export const isPartial = (s: Pick<XvalState, "meta" | "rows">): boolean => s.meta !== null && s.rows.length < s.meta.total;

export function resetXval(): void {
  seq++;
  if (errorsShown()) hideErrors();
  xvalStore.set({ ...IDLE_XVAL });
}

/** The row the window works on; its table is read. null — the window waits for a choice. */
export function openXval(id: number | null): Promise<void> {
  const cur = xvalStore.get();
  if (id !== null && id === cur.estimationId && cur.phase !== "idle") return Promise.resolve();
  seq++;
  if (errorsShown()) hideErrors();
  xvalStore.set({ ...IDLE_XVAL, estimationId: id, by: cur.by, size: cur.size, tab: cur.tab });
  return id === null ? Promise.resolve() : loadXval();
}

const uidNow = (): string | null => projectStore.get().current?.uid ?? null;

/** Reads the result: the first page gives the summary and the histogram, the rest of the table follows page by page. */
export async function loadXval(): Promise<void> {
  const id = xvalStore.get().estimationId;
  if (id === null) return;
  const mine = ++seq;
  const uid = uidNow();
  const live = () => mine === seq && uidNow() === uid;
  xvalStore.set({ phase: "loading", error: null, reading: null });
  try {
    const first = await loadXvalPage(id, 1, PAGE_MAX);
    if (!live()) return;
    let rows = first.rows;
    const of = Math.min(first.pages, Math.ceil(ROWS_MAX / PAGE_MAX));
    xvalStore.set({ phase: "ready", meta: metaOf(first), rows, tablePage: 1, reading: of > 1 ? { done: 1, of } : null, note: null });
    for (let p = 2; p <= of; p++) {
      const next = await loadXvalPage(id, p, PAGE_MAX);
      if (!live()) return;
      if (next.at !== first.at) return void (await loadXval()); // calculated again while it was read
      rows = rows.concat(next.rows);
      xvalStore.set({ rows, reading: p < of ? { done: p, of } : null });
    }
    xvalStore.set({ reading: null });
    if (errorsShown()) refresh3d();
  } catch (e) {
    if (!live()) return;
    if (e instanceof CoreError && e.code === NOT_FOUND) {
      if (errorsShown()) hideErrors();
      xvalStore.set({ phase: "none", meta: null, rows: [], reading: null, shown: null });
    } else xvalStore.set({ phase: xvalStore.get().meta ? "ready" : "failed", reading: null, error: refusalOf(e) });
  }
}

/** The calculation may have been made again or its row changed: ask for the first row only and read all again when it is another. */
export async function syncXval(): Promise<void> {
  const s = xvalStore.get();
  if (s.estimationId === null || s.phase === "running" || s.phase === "loading") return;
  const id = s.estimationId;
  const uid = uidNow();
  try {
    const p = await loadXvalPage(id, 1, 1);
    const now = xvalStore.get();
    if (now.estimationId !== id || uidNow() !== uid || now.phase === "running" || now.phase === "loading") return;
    if (!now.meta || p.at !== now.meta.at) await loadXval();
    else if (p.stale !== now.meta.stale) xvalStore.set({ meta: { ...now.meta, stale: p.stale } });
  } catch (e) {
    if (e instanceof CoreError && e.code === NOT_FOUND && xvalStore.get().meta) await loadXval();
  }
}

const nameOf = (id: number): string => rowById(id)?.name ?? `№${id}`;

/** The job `estimate.xval`: followed to its end; the table is read again; the answer is the line with the summary. */
export async function runXval(by: XvalBy): Promise<string> {
  const s = xvalStore.get();
  const id = s.estimationId;
  if (id === null) throw refusal("bad_param", "Не выбрана строка «Параметры оценки».", "Выберите строку в окне или передайте estimation_id.");
  if (s.phase === "running") throw refusal("busy", "Перекрёстная проверка уже выполняется.", "Дождитесь конца задания или отмените его во вкладке «Задания».");
  const before: XvalPhase = s.meta ? "ready" : "none";
  const mine = ++seq;
  const uid = uidNow();
  xvalStore.set({ phase: "running", by, error: null, note: null, job: null });
  const back = (patch: Partial<XvalState>) => xvalStore.set({ phase: before, job: null, ...patch });
  try {
    const started = await startXval({ estimation_id: id, by });
    if (mine === seq) xvalStore.set({ job: started });
    const end = await waitJob(started);
    if (mine !== seq || uidNow() !== uid) return "Проект или строка изменились: результат больше не показывается";
    if (end.status === "cancelled") {
      const note = `Перекрёстная проверка «${nameOf(id)}» отменена: ${before === "ready" ? "прежний результат не изменён" : "ничего не записано"}`;
      back({ note });
      return note;
    }
    if (end.status === "failed") throw refusal(end.error?.code ?? "job_failed", end.error?.message ?? "Перекрёстная проверка не выполнена.", end.error?.hint ?? "Повторите проверку.");
    const done = await loadJob(started);
    const r = readXvalResult(done.result);
    if (!r) throw refusal("bad_answer", "Ядро вернуло перекрёстную проверку без итогов.", "Повторите проверку; если повторится — обновите программу.");
    xvalStore.set({ job: null });
    await loadXval();
    return resultLine(nameOf(id), r.by, r.summary);
  } catch (e) {
    const r = refusalOf(e);
    if (mine === seq) back({ error: r });
    throw refusal(r.code, r.message, r.hint);
  }
}

// ---- the table ----

export const setBy = (by: XvalBy): void => xvalStore.set({ by });
export const setTab = (tab: XvalTab): void => xvalStore.set({ tab });
export const setSort = (key: SortKey): void => xvalStore.set((s) => ({ sort: nextSort(s.sort, key), tablePage: 1 }));
export const setTablePage = (page: number): void => xvalStore.set({ tablePage: Math.max(1, Math.floor(page)) });
export const setSize = (size: number): void => xvalStore.set({ size, tablePage: 1 });

// ---- the errors in 3D ----

function refresh3d(): void {
  const cloud = errorCloud(xvalStore.get().rows);
  if (!cloud) {
    hideErrors();
    xvalStore.set({ shown: null });
    return;
  }
  showErrors(cloud);
  xvalStore.set({ shown: { n: cloud.n, limit: cloud.limit } });
}

/** The samples with an error, coloured by it, in the 3D window; the old ones leave. */
export function show3d(): ErrorCloud {
  const cloud = errorCloud(xvalStore.get().rows);
  if (!cloud) throw refusal("no_errors", "Нет проб с ошибкой и положением: показывать нечего.", "Выполните перекрёстную проверку.");
  showErrors(cloud);
  xvalStore.set({ shown: { n: cloud.n, limit: cloud.limit } });
  return cloud;
}

export function hide3d(): void {
  hideErrors();
  xvalStore.set({ shown: null });
}

/** Why the table cannot be drawn in 3D now, or null. */
export function reason3d(s: Pick<XvalState, "rows" | "phase">): string | null {
  if (s.phase === "running") return "Идёт перекрёстная проверка";
  return s.rows.some((r) => r.error !== null && r.x !== null) ? null : "Нет результата: выполните перекрёстную проверку";
}

/** The row the stale words speak of: its own reason (`stale_reason`) when it is out of date itself. */
export const staleReasonOf = (id: number | null): string | null => (id === null ? null : (estimationStore.get().details[id]?.stale_reason ?? rowById(id)?.stale_reason ?? null));
