/**
 * The analysis of the neighbourhood of a kriging row, KNA (ТЗ 8.8.5; ADR 0024 решение 14, № 80; docs/progress/e18-api.md §8.2): what the
 * window «Анализ окрестности» and the command `estimate.kna` share — the row, the parameters to vary, the test blocks (three picked by the
 * core as «много / средне / мало», or up to ten of the person's own), the job and its result, the words of the recommendation. The core
 * only recommends an `n_max` (the smallest with SLOPE ≥ 0,95 of the best); it is never applied by itself: «Применить к параметрам…»
 * opens the row in the wizard with the value put in (the person saves it there, `applyNMax`). One analysis at a time. No React.
 */
import type { App } from "../../shell/app";
import { askConfirm } from "../../shell/dialogs";
import { refusal } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import { parseNumber } from "../../charts/core/axisScale";
import { cancelJob } from "../jobs/state";
import { refusalOf, STATUS_LABELS, type EstimationRow, type Refusal, type Started } from "./api";
import { KNA_PARAMS, KNA_TITLES, readKna, startKna, type BlockAt, type KnaBlock, type KnaBody, type KnaParam, type KnaResult } from "./checksApi";
import { followJob } from "./checksRun";
import { estimationStore } from "./state";
import { setForm, setStep, wizardStore } from "./wizardState";

export const KNA_WINDOW = "estimation-kna";
export const KNA_TITLE = "Анализ окрестности";
export const BLOCKS_MAX = 10;

export type KnaPhase = "idle" | "running" | "done" | "failed" | "cancelled";
export type PickKind = "ijk" | "xyz";

export const PICK_TITLES: Record<PickKind, string> = { ijk: "Индексы родителя i; j; k", xyz: "Точка x; y; z, м" };

/** A test block the person pointed at, as typed (checked when added). */
export interface BlockPick {
  kind: PickKind;
  text: string;
  at: BlockAt;
}

export interface KnaState {
  rowId: number | null;
  /** the parameters to run (one at a time each, the others as the row has them) */
  vary: KnaParam[];
  mode: "auto" | "own";
  picks: BlockPick[];
  draftKind: PickKind;
  draftText: string;
  /** why the draft was not added */
  draftIssue: string | null;
  /** the parameter whose charts and table are shown */
  shown: KnaParam;
  /** the block of the table (from 0) */
  blockIndex: number;
  phase: KnaPhase;
  job: Started | null;
  result: KnaResult | null;
  error: Refusal | null;
  /** «отменено»: a line, not an error */
  note: string | null;
}

const fresh = (): KnaState => ({ rowId: null, vary: [...KNA_PARAMS], mode: "auto", picks: [], draftKind: "ijk", draftText: "", draftIssue: null, shown: "n_max", blockIndex: 0, phase: "idle", job: null, result: null, error: null, note: null });

export const knaStore = new Store<KnaState>(fresh());

export const resetKna = (): void => knaStore.set(fresh());

/** Another row or other settings: what was shown belongs to the old ones. */
const drop = { phase: "idle", job: null, result: null, error: null, note: null, blockIndex: 0 } as const;

export function setKnaRow(id: number | null): void {
  if (knaStore.get().phase === "running") return;
  knaStore.set({ rowId: id, ...drop });
}

export function toggleVary(p: KnaParam): void {
  const s = knaStore.get();
  const vary = s.vary.includes(p) ? s.vary.filter((x) => x !== p) : KNA_PARAMS.filter((x) => x === p || s.vary.includes(x));
  knaStore.set({ vary, shown: vary.includes(s.shown) ? s.shown : (vary[0] ?? s.shown) });
}

export const setMode = (mode: "auto" | "own"): void => knaStore.set({ mode });
export const setDraftKind = (kind: PickKind): void => knaStore.set({ draftKind: kind, draftIssue: null });
export const setDraftText = (text: string): void => knaStore.set({ draftText: text, draftIssue: null });
export const setShown = (p: KnaParam): void => knaStore.set({ shown: p });
export const setBlockIndex = (i: number): void => knaStore.set({ blockIndex: i });
export const removePick = (i: number): void => knaStore.set((s) => ({ picks: s.picks.filter((_, k) => k !== i) }));

// ---- the blocks the person points at ----

export type Parsed<T> = { ok: true; value: T } | { ok: false; message: string };

/** «4; 4; 7» → three numbers (`;` or spaces between them; a decimal comma is a comma). */
export function triple(text: string): number[] | null {
  const parts = text.trim().split(/[;\s]+/).filter(Boolean);
  if (parts.length !== 3) return null;
  const values = parts.map((p) => parseNumber(p));
  return values.every((v): v is number => v !== null) ? values : null;
}

export function parseBlock(kind: PickKind, text: string): Parsed<BlockAt> {
  const v = triple(text);
  if (!v) return { ok: false, message: kind === "ijk" ? "Блок: нужны три индекса, например 4; 4; 7." : "Точка: нужны три координаты, например 500040; 6700040; 297,5." };
  if (kind === "xyz") return { ok: true, value: { xyz: [v[0], v[1], v[2]] } };
  if (v.some((x) => !Number.isInteger(x) || x < 0)) return { ok: false, message: "Индексы блока — целые числа от 0." };
  return { ok: true, value: { ijk: [v[0], v[1], v[2]] } };
}

/** «Добавить»: the draft becomes a test block (up to ten, no repeats). */
export function addPick(): boolean {
  const s = knaStore.get();
  const no = (draftIssue: string): false => {
    knaStore.set({ draftIssue });
    return false;
  };
  if (s.picks.length >= BLOCKS_MAX) return no(`Тестовых блоков не больше ${BLOCKS_MAX}.`);
  const got = parseBlock(s.draftKind, s.draftText);
  if (!got.ok) return no(got.message);
  const text = s.draftText.trim();
  if (s.picks.some((p) => p.kind === s.draftKind && JSON.stringify(p.at) === JSON.stringify(got.value))) return no("Такой блок уже добавлен.");
  knaStore.set({ picks: [...s.picks, { kind: s.draftKind, text, at: got.value }], draftText: "", draftIssue: null, mode: "own" });
  return true;
}

export const pickText = (p: Pick<BlockPick, "kind" | "text">): string => (p.kind === "ijk" ? `Родитель ${p.text}` : `Точка ${p.text}`);

// ---- the rows, the request ----

/** The kriging rows (SK, OK): the core refuses the others (422 `kna_method`). */
export const knaRows = (rows: readonly EstimationRow[] = estimationStore.get().rows): EstimationRow[] => rows.filter((r) => r.method === "ok" || r.method === "sk");

export const KNA_METHOD_MESSAGE = "Анализ окрестности — только для простого и обычного кригинга.";
export const KNA_METHOD_HINT = "Выберите строку с методом SK или OK.";

export type Built = { ok: true; body: KnaBody } | { ok: false; message: string; hint: string };

/** The body of the request over the state: why it cannot be made — in words. */
export function knaBody(s: Pick<KnaState, "rowId" | "vary" | "mode" | "picks">): Built {
  if (s.rowId === null) return { ok: false, message: "Не выбрана строка оценки.", hint: "Выберите строку «Параметры оценки» с методом SK или OK." };
  const row = estimationStore.get().rows.find((r) => r.id === s.rowId);
  if (!row && estimationStore.get().loaded) return { ok: false, message: `Строка «Параметры оценки» №${s.rowId} не найдена.`, hint: "Номера строк — в разделе «Геостатистика → Параметры оценки» дерева." };
  if (row && row.method !== "ok" && row.method !== "sk") return { ok: false, message: KNA_METHOD_MESSAGE, hint: KNA_METHOD_HINT };
  if (row?.source_lost) return { ok: false, message: "Источник параметров оценки удалён.", hint: "Восстановите таблицу, модель вариограммы или блочную модель либо удалите строку." };
  if (!s.vary.length) return { ok: false, message: "Не выбраны параметры для перебора.", hint: "Отметьте n_max, радиус, дискретизацию или размер блока." };
  if (s.mode === "own" && !s.picks.length) return { ok: false, message: "Не добавлено ни одного тестового блока.", hint: "Добавьте блок или выберите автоматический выбор: много, средне, мало проб." };
  return { ok: true, body: { estimation_id: s.rowId, vary: KNA_PARAMS.filter((p) => s.vary.includes(p)), ...(s.mode === "own" ? { blocks: s.picks.map((p) => p.at) } : {}) } };
}

/** Why the analysis cannot be started now; null — it can. */
export function knaReason(s: KnaState): string | null {
  if (s.phase === "running") return "Анализ уже выполняется";
  const b = knaBody(s);
  return b.ok ? null : b.message;
}

// ---- the run ----

let seq = 0;

/** The job `estimate.kna` from the start to the end; the answer is the words about the recommendation. */
export async function runKna(): Promise<string> {
  const s = knaStore.get();
  if (s.phase === "running") throw refusal("busy", "Анализ окрестности уже выполняется.", "Дождитесь конца или отмените его.");
  const built = knaBody(s);
  if (!built.ok) throw refusal(built.message === KNA_METHOD_MESSAGE ? "kna_method" : "bad_param", built.message, built.hint);
  const mine = ++seq;
  const live = () => mine === seq;
  knaStore.set({ ...drop, phase: "running" });
  try {
    const started = await startKna(built.body);
    if (live()) knaStore.set({ job: started });
    const end = await followJob(started, { message: "Анализ окрестности не выполнен.", hint: "Повторите анализ." });
    if (end.kind === "stale") {
      if (live()) resetKna();
      return "Проект изменился: результат больше не показывается";
    }
    if (end.kind === "cancelled") {
      const note = "Анализ окрестности отменён: ничего не записано";
      if (live()) knaStore.set({ phase: "cancelled", job: null, note });
      return note;
    }
    const result = readKna(end.result);
    if (!result) throw refusal("bad_answer", "Ядро вернуло анализ без итогов.", "Повторите анализ; если повторится — обновите программу.");
    if (live()) knaStore.set({ phase: "done", job: null, result, shown: result.vary.includes(knaStore.get().shown) ? knaStore.get().shown : (result.vary[0] ?? "n_max"), blockIndex: 0 });
    return knaSummary(result);
  } catch (e) {
    const r = refusalOf(e);
    if (live()) knaStore.set({ phase: "failed", job: null, error: r });
    throw refusal(r.code, r.message, r.hint);
  }
}

/** «Отменить»: the job stops, nothing is written (the analysis writes nothing at all). */
export async function cancelKna(): Promise<void> {
  const job = knaStore.get().job;
  if (job) await cancelJob(job);
}

// ---- the words ----

const nf = (x: number, digits = 3): string => x.toLocaleString("ru-RU", { maximumFractionDigits: digits });
export const num = (x: number | null, digits = 3): string => (x === null ? "—" : nf(x, digits));

/** «много · родитель 4; 4; 7 · проб в радиусе первого прохода 94». */
export function blockTitle(b: KnaBlock, i: number): string {
  const [a, c, d] = b.at.ijk;
  return `${b.auto ?? `Блок ${i + 1}`} · родитель ${a}; ${c}; ${d} · проб в r₁: ${b.n_r1.toLocaleString("ru-RU")}`;
}

/** The short name of a block in a chart or a tab. */
export const blockShort = (b: KnaBlock, i: number): string => b.auto ?? `Блок ${i + 1}`;

/** The status of a variant that has no numbers: «вне домена», «мало проб», «вырожденная система». */
export const statusWord = (status: number): string => STATUS_LABELS[status] ?? `статус ${status}`;

/** The recommendation of the core in words (it is never applied by itself). */
export function recommendText(r: KnaResult): string {
  if (!r.recommended) return "n_max не перебирался — рекомендации нет. Отметьте n_max и повторите анализ.";
  const share = Math.round(r.threshold * 100);
  if (r.recommend === null) return `Рекомендации нет: у блоков нет оценки или наклон регрессии SLOPE не выше 0 ни при одном n_max.`;
  const many = r.blocks.length > 1 ? ` — наибольшее из рекомендаций блоков (так наклон держится у самого требовательного из ${r.blocks.length})` : "";
  return `Рекомендуется n_max = ${r.recommend}: наименьшее число проб, при котором SLOPE не ниже ${share} % от лучшего${many}.`;
}

/** The answer of the command: the recommendation and the counts. */
export function knaSummary(r: KnaResult): string {
  const blocks = r.blocks.map((b, i) => `${blockShort(b, i)} (n_max ${b.recommend ?? "—"})`).join(", ");
  const warn = r.warnings.length ? ` ${r.warnings.join(" ")}` : "";
  return `Анализ окрестности «${r.name}»: ${recommendText(r)} Блоков: ${r.blocks.length} — ${blocks}. Не применяется само: «Применить к параметрам…» откроет правку строки.${warn}`;
}

/** The titles of the parameters run, as «n_max — число проб; Радиус поиска». */
export const varyText = (vary: readonly KnaParam[]): string => vary.map((p) => KNA_TITLES[p]).join("; ");

// ---- «Применить к параметрам…» ----

/** The stored `search` with `n_max` of every pass put in (`n_min` cut to it, as the analysis did); null — there are no passes to put it in. */
export function withNMax(search: unknown, nMax: number): Record<string, unknown> | null {
  if (!search || typeof search !== "object" || Array.isArray(search)) return null;
  const s = search as Record<string, unknown>;
  if (!Array.isArray(s.passes) || !s.passes.length) return null;
  const passes = s.passes.map((p) => {
    const o = p && typeof p === "object" ? (p as Record<string, unknown>) : {};
    const min = typeof o.n_min === "number" ? Math.min(o.n_min, nMax) : o.n_min;
    return { ...o, n_max: nMax, ...(min === undefined ? {} : { n_min: min }) };
  });
  return { ...s, passes };
}

/**
 * The row in the wizard («Изменить») with `n_max` put into the passes of its search — not saved: the person looks at it in the step
 * «Поиск и проходы» and saves it (or not). The wizard that holds other unsaved changes is not replaced without asking.
 */
export async function applyNMax(app: App, rowId: number, nMax: number): Promise<string> {
  const w = wizardStore.get();
  if (w.dirty && w.editing?.id !== rowId && !(await askConfirm({ title: "Мастер «Оценка содержаний»", message: "В мастере есть несохранённые изменения. Заменить их строкой из анализа окрестности?", okLabel: "Заменить", danger: true }))) return "Отменено";
  const done = await app.registry.execute("estimate.open", { id: rowId }, "user");
  if (!done.ok) throw refusal("open_failed", done.result || "Мастер не открылся.", "Откройте строку командой «Оценка содержаний».");
  const f = wizardStore.get().form;
  const search = withNMax(f.rest.search, nMax);
  if (!search) throw refusal("no_passes", "В строке нет проходов поиска.", "Задайте проходы на шаге «Поиск и проходы» мастера.");
  setForm({ rest: { ...f.rest, search } });
  setStep(4);
  return `Строка №${rowId} открыта в мастере: n_max = ${nMax} во всех проходах. Проверьте шаг «Поиск и проходы» и сохраните строку`;
}
