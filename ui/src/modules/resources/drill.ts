/**
 * The transition from a cell of the report to its blocks (ТЗ 7.35 Д-36, ТД36-1; ADR 0027 решения 22–24, вопрос № 111): a click on a cell of a saved report
 * (`setCellClickHandler`, 20.13) or the command `resources.drill` asks the core for the rules and the numbers of the cell (`POST …/drill`), puts the rules as
 * the temporary restrictor of the model in the 3D (`quick.rule` of the style: it is joined to the quick restrictor by «И» and is never written to the library of
 * filters; «Сбросить», closing the window, another project — and the style is as it was), opens the window «Переход к блокам» with the numbers (they are the
 * numbers of the cell) and asks for the table of the blocks of the cell by pages of 1000 (`POST …/drill/rows`; the CSV of a page is saved in the save window of the
 * application). A block of the table goes to the inspector of Д-30 (`estimate.inspect`), whose sample opens the interval of 7.17. A cell of the form that is a
 * sum of cells (a section, a subtotal) is not one cell of the core: the person is told so. A gap is «—», never 0. No React.
 */
import { bridge } from "../../bridge";
import { api, CoreError } from "../../shell/api";
import type { App } from "../../shell/app";
import { viewport } from "../../shell/objectRun";
import { Store } from "../../shell/store";
import { focusWindow, openWindow, updateWindow, windowStore } from "../../shell/windows";
import { BLOCK_STYLES_KEY, revealOnShow, styleIn, styleOp } from "../blocks/display";
import type { Rule } from "../blocks/filters/constructor";
import { blockById } from "../blocks/state";
import { projectStore } from "../projects/state";
import { updateView, viewStore } from "../scene/bind";
import { obj, readDrill, str, num, type DrillAnswer } from "./answers";
import { drillCell, refusalOf, type DrillBody, type Refusal } from "./api";
import { setCellClickHandler, type CellRef } from "./cells";
import { exportFileName } from "./exportFile";
import { fmt, fmtCutoff } from "./format";

export const DRILL_WINDOW = "resources-drill";
export const DRILL_TITLE = "Переход к блокам";
export const PAGE_SIZE = 1000;

// ---- the answers of the core ----

/** A block of the cell (`POST …/drill/rows`): the parent cell i, j, k, the centre and the sizes (m), the share inside, the volume and the tonnes; a gap is null. */
export interface DrillRow {
  i: number;
  j: number;
  k: number;
  x: number | null;
  y: number | null;
  z: number | null;
  dx: number | null;
  dy: number | null;
  dz: number | null;
  f: number | null;
  volume: number | null;
  tonnes: number | null;
  keys: Record<string, string | null>;
  elements: Record<string, number | null>;
}

/** The page of the blocks of the cell: the answer of `drill` and the rows. */
export interface DrillPageAnswer extends DrillAnswer {
  page: number;
  size: number;
  total: number;
  rows: DrillRow[];
  /** `csv: true` — the text of the page (`;`, a decimal comma, CRLF); else null */
  csv: string | null;
}

const int = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null);

export function readDrillRow(raw: unknown): DrillRow | null {
  const r = obj(raw);
  const i = int(r.i);
  const j = int(r.j);
  const k = int(r.k);
  if (i === null || j === null || k === null) return null;
  const keys: Record<string, string | null> = {};
  for (const [name, v] of Object.entries(obj(r.keys))) keys[name] = typeof v === "string" ? v : v === null ? null : String(v);
  const elements: Record<string, number | null> = {};
  for (const [name, v] of Object.entries(obj(r.elements))) elements[name] = num(v);
  return { i, j, k, x: num(r.x), y: num(r.y), z: num(r.z), dx: num(r.dx), dy: num(r.dy), dz: num(r.dz), f: num(r.f), volume: num(r.volume), tonnes: num(r.tonnes), keys, elements };
}

export function readDrillPage(raw: unknown): DrillPageAnswer | null {
  const base = readDrill(raw);
  if (!base) return null;
  const d = obj(raw);
  const rows = (Array.isArray(d.rows) ? d.rows : []).map(readDrillRow).filter((r): r is DrillRow => r !== null);
  return { ...base, page: int(d.page) ?? 1, size: int(d.size) ?? PAGE_SIZE, total: int(d.total) ?? rows.length, rows, csv: str(d.csv) };
}

/** `POST /resources/reports/{id}/drill/rows`: the blocks of the cell, one page (synchronous). */
export async function drillRows(id: number, body: DrillBody, page: number, csv = false): Promise<DrillPageAnswer> {
  const r = readDrillPage(await api.post(`/resources/reports/${id}/drill/rows`, { ...body, page, size: PAGE_SIZE, ...(csv && { csv: true }) }));
  if (!r) throw new CoreError(0, "bad_answer", "Ядро не вернуло блоки ячейки.", "Обновите программу.");
  return r;
}

/** The pages of `total` rows (at least one: an empty cell is a page without rows). */
export const pagesOf = (total: number, size = PAGE_SIZE): number => Math.max(1, Math.ceil(total / size));

// ---- the cell → the request ----

/** What a window knows of the cell asked for. */
export interface DrillTarget {
  reportId: number;
  reportName: string;
  /** «Измеренные / A», «Итого» */
  label: string;
  body: DrillBody;
}

/** The body of the request from a cell of the table: `kind` only when it is not the one the cut-off tells (a number — `cutoff`, none — `all`). */
export function bodyOfCell(cell: Pick<CellRef, "group" | "cutoff" | "kind">): DrillBody {
  const body: DrillBody = { group: cell.group && cell.group.length ? [...cell.group] : null, cutoff: cell.cutoff };
  const derived = cell.cutoff === null ? "all" : "cutoff";
  if (cell.kind !== derived) body.kind = cell.kind;
  return body;
}

/** The title of a cell: the group, the row (the cut-off, balance, off-balance). */
export function cellTitle(label: string, body: DrillBody): string {
  const c = body.cutoff;
  const row = body.kind === "balance" ? `балансовые${c === null ? "" : ` ≥ ${fmtCutoff(c)}`}` : body.kind === "offbalance" ? `забалансовые${c === null ? "" : ` ${fmtCutoff(c)}`}` : c === null ? "без борта" : `борт ≥ ${fmtCutoff(c)}`;
  return `${label}, ${row}`;
}

/** The target of a click, or why the cell is not one cell of the core (in the words of the window). */
export function targetOfCell(cell: CellRef, reportName: (id: number) => string): { ok: true; target: DrillTarget } | { ok: false; refusal: Refusal } {
  if (cell.reportId === null) return { ok: false, refusal: { code: "drill_preview", message: "Предпросмотр нельзя раскрыть до блоков: ядро не хранит его таблицу.", hint: "Сохраните отчёт и щёлкните по ячейке сохранённого отчёта.", param: null } };
  if (!cell.exact) {
    const what = cell.members ? "подытог — сумма групп" : cell.section ? "ячейка формы с разделами — сумма ячеек таблицы" : "ячейка формы — сумма ячеек таблицы";
    return { ok: false, refusal: { code: "drill_not_exact", message: `Эта ячейка не одна ячейка таблицы: ${what}.`, hint: "Щёлкните по ячейке строки таблицы: её блоки равны ячейке точно.", param: null } };
  }
  const body = bodyOfCell(cell);
  return { ok: true, target: { reportId: cell.reportId, reportName: reportName(cell.reportId), label: cell.label, body } };
}

// ---- the state of the window ----

export type Load = "idle" | "loading" | "ready" | "failed";

export interface DrillState {
  target: DrillTarget | null;
  load: Load;
  answer: DrillAnswer | null;
  error: Refusal | null;
  /** the words under the numbers (the 3D could not take the filter, …) */
  notes: string[];
  page: number;
  rows: { load: Load; data: DrillPageAnswer | null; error: Refusal | null };
  /** the temporary restrictor is in the 3D now */
  applied: boolean;
  /** the answer of the save window of the CSV */
  csvNote: { text: string; error: boolean } | null;
  /** the answer of the inspector (a block that has no estimation) */
  inspectNote: string | null;
}

const NO_ROWS: DrillState["rows"] = { load: "idle", data: null, error: null };
const INITIAL: DrillState = { target: null, load: "idle", answer: null, error: null, notes: [], page: 1, rows: NO_ROWS, applied: false, csvNote: null, inspectNote: null };

export const drillStore = new Store<DrillState>(INITIAL);

let seq = 0;
/** What the style of the model was before the filter: put back by `clearFilter`. */
let restore: { modelId: number; rule: Rule | null } | null = null;

export function resetDrill(): void {
  seq++;
  restore = null;
  drillStore.set({ ...INITIAL });
}

/** The window «Переход к блокам», if open. */
export const windowOfDrill = (): string | null => windowStore.get().windows.find((w) => w.kind === DRILL_WINDOW)?.id ?? null;

function openDrillWindow(title: string): void {
  const open = windowOfDrill();
  if (open) {
    focusWindow(open);
    updateWindow(open, { title });
    return;
  }
  openWindow(DRILL_WINDOW, title, "normal", {}, ...viewport());
}

// ---- the temporary filter of the 3D ----

/** Puts the rules of the cell as the restrictor of the model (shown if it was hidden); the words of what did not go — empty when fine. */
export function applyFilter(modelId: number | null, rules: Record<string, unknown>): string[] {
  if (modelId === null) return ["Ядро не назвало модель ячейки: фильтр в 3D не применён."];
  if (!blockById(modelId)) return [`Блочная модель №${modelId} не найдена в проекте: фильтр в 3D не применён.`];
  if (restore && restore.modelId !== modelId) clearFilter();
  const before = styleIn(viewStore.get(), modelId);
  try {
    if (!before.visible) revealOnShow.add(modelId);
    updateView(BLOCK_STYLES_KEY, styleOp(modelId, { visible: true, quick: { rule: rules as unknown as Rule } }));
  } catch (e) {
    return [`Фильтр в 3D не применён: ${e instanceof Error ? e.message : String(e)}.`];
  }
  if (!restore) restore = { modelId, rule: before.quick.rule ?? null };
  drillStore.set({ applied: true });
  return [];
}

/** «Сбросить»: the restrictor of the model is the one it was before the first transition. */
export function clearFilter(): void {
  const r = restore;
  restore = null;
  if (drillStore.get().applied) drillStore.set({ applied: false });
  if (!r) return;
  try {
    updateView(BLOCK_STYLES_KEY, styleOp(r.modelId, { quick: { rule: r.rule } }));
  } catch {
    /* the project is gone: the view with it */
  }
}

/** «Показать в 3D»: the filter of the cell again (after «Сбросить»). */
export function reapplyFilter(): void {
  const a = drillStore.get().answer;
  if (!a) return;
  drillStore.set({ notes: applyFilter(a.model_id, a.rules) });
}

// ---- the cell ----

/** The numbers and the rules of the cell; the window opens at once, the 3D takes the filter, the first page of the blocks is read. The answer — null when refused (the window says why). */
export async function showCell(target: DrillTarget): Promise<DrillAnswer | null> {
  const my = ++seq;
  drillStore.set({ ...INITIAL, target, load: "loading", applied: drillStore.get().applied });
  openDrillWindow(`${DRILL_TITLE} · ${cellTitle(target.label, target.body)}`);
  let answer: DrillAnswer;
  try {
    answer = await drillCell(target.reportId, target.body);
  } catch (e) {
    if (my === seq) drillStore.set({ load: "failed", error: refusalOf(e) });
    return null;
  }
  if (my !== seq) return null;
  const notes = applyFilter(answer.model_id, answer.rules);
  drillStore.set({ load: "ready", answer, notes });
  void loadPage(1);
  return answer;
}

/** A cell that cannot be drilled: the window with the reason. */
export function refuseCell(refusal: Refusal): void {
  seq++;
  drillStore.set({ ...INITIAL, load: "failed", error: refusal, applied: drillStore.get().applied });
  openDrillWindow(DRILL_TITLE);
}

/** The page of the blocks of the cell (1 … pages). */
export async function loadPage(page: number): Promise<void> {
  const t = drillStore.get().target;
  if (!t) return;
  const my = seq;
  drillStore.set({ page, rows: { load: "loading", data: drillStore.get().rows.data, error: null }, csvNote: null, inspectNote: null });
  try {
    const data = await drillRows(t.reportId, t.body, page);
    if (my !== seq) return;
    drillStore.set({ rows: { load: "ready", data, error: null } });
  } catch (e) {
    if (my === seq) drillStore.set({ rows: { load: "failed", data: null, error: refusalOf(e) } });
  }
}

/** The name of the file of the CSV of a page. */
export function csvName(t: DrillTarget, page: number): string {
  return exportFileName(`Блоки ячейки — ${t.reportName} — ${cellTitle(t.label, t.body)} — стр. ${page}`, "csv");
}

/** «Сохранить страницу CSV…»: the page again with `csv: true`, then the save window of the application (`bridge().saveFile`, with a BOM). */
export async function saveCsv(): Promise<void> {
  const t = drillStore.get().target;
  const page = drillStore.get().page;
  if (!t) return;
  const b = bridge();
  if (!b) {
    drillStore.set({ csvNote: { text: "Сохранение файлов доступно только в приложении.", error: true } });
    return;
  }
  try {
    const data = await drillRows(t.reportId, t.body, page, true);
    if (data.csv === null) {
      drillStore.set({ csvNote: { text: "Ядро не вернуло текст страницы.", error: true } });
      return;
    }
    const r = await b.saveFile(csvName(t, page), "csv", data.csv);
    if (r === null) return; // the window was closed: nothing is written
    drillStore.set({ csvNote: typeof r === "object" ? { text: r.error, error: true } : { text: `Страница ${page} записана в файл ${r}`, error: false } });
  } catch (e) {
    const r = refusalOf(e);
    drillStore.set({ csvNote: { text: `${r.message} ${r.hint}`.trim(), error: true } });
  }
}

/** A block of the table → the inspector of Д-30 (`estimate.inspect`: the probes of the estimate, from a probe the interval of 7.17). */
export async function inspectRow(app: App, row: DrillRow): Promise<void> {
  const model = drillStore.get().answer?.model_id;
  if (model === null || model === undefined) return;
  const why = app.registry.reason("estimate.inspect");
  if (why) {
    drillStore.set({ inspectNote: `Инспектор блока недоступен: ${why}.` });
    return;
  }
  drillStore.set({ inspectNote: null });
  const r = await app.registry.execute("estimate.inspect", { model_id: model, block: `${row.i}; ${row.j}; ${row.k}` }, "user");
  if (!r.ok) drillStore.set({ inspectNote: r.result });
}

/** The columns of the table of the blocks: the keys and the elements the rows have, in the order of the first row that has them. */
export function columnsOf(rows: readonly DrillRow[]): { keys: string[]; elements: string[] } {
  const keys: string[] = [];
  const elements: string[] = [];
  for (const r of rows) {
    for (const k of Object.keys(r.keys)) if (!keys.includes(k)) keys.push(k);
    for (const e of Object.keys(r.elements)) if (!elements.includes(e)) elements.push(e);
  }
  return { keys, elements };
}

/** A number of the table: a gap is «—», never 0. */
export const cellText = (v: number | null | undefined): string => (v === null || v === undefined ? "—" : fmt(v));

let unbindDrill: (() => void) | null = null;

/** The binding: a click on a cell of a saved report goes here (the window of the report calls `emitCellClick`). One app at a time (the tests make several): the earlier binding goes. */
export function bindDrill(app: App, reportName: (id: number) => string): () => void {
  unbindDrill?.();
  const offClick = setCellClickHandler((cell) => {
    const t = targetOfCell(cell, reportName);
    if (!t.ok) return refuseCell(t.refusal);
    const body = t.target.body;
    // the same path as the assistant and Ctrl+K (the record in the log, the notice on a refusal); a label with «;» cannot be said in the string of the command
    if ((body.group ?? []).some((l) => l.includes(";"))) return void showCell(t.target);
    void app.registry.execute("resources.drill", { report_id: t.target.reportId, ...(body.group && { group: body.group.join("; ") }), ...(body.cutoff !== null && { cutoff: body.cutoff }), ...(body.kind && { kind: body.kind }) }, "user").catch(() => undefined);
  });
  let uid = projectStore.get().current?.uid ?? null;
  const offProject = projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetDrill();
  });
  // the filter is temporary: with the window closed the 3D is as it was
  const offWindow = windowStore.subscribe(() => {
    if (restore && !windowOfDrill()) clearFilter();
  });
  const off = () => {
    [offClick, offProject, offWindow].forEach((f) => f());
    if (unbindDrill === off) unbindDrill = null;
  };
  unbindDrill = off;
  return off;
}
