/**
 * The state of the bottom tab «Ресурсы» (ТЗ 8.10 «Вывод», «Интерфейс»; ADR 0027 решения 16, 19, 29): what the panel shows — the result of the last run of the window
 * (the preview or the saved one: `resourcesStore.run.result`) or a saved report opened from the tree (`GET /resources/reports/{id}`) — the tab, the template and the
 * cut-off of the form, the page of the rows, the element of the chart. The form is made by the core (`text` of a cell is shown as it is): a template or a cut-off
 * of another choice is asked of the core again (a saved report only: the core keeps nothing of a preview). No React.
 */
import { Store } from "../../shell/store";
import { errorText } from "../../shell/props";
import { setPanel, type BottomTab } from "../../shell/workspace";
import type { App } from "../../shell/app";
import type { ReportResult } from "./answers";
import { loadReport } from "./api";
import { resourcesStore } from "./state";

/** The id of the tab in the bottom dock (`BottomTab` of the shell gets `resources` — by the orchestrator). */
export const RESOURCES_TAB = "resources" as BottomTab;
export const RESOURCES_TAB_TITLE = "Ресурсы";

export type PanelTab = "form" | "table" | "curve" | "control";
export type RightAxisKind = "grade" | "metal";

/** The rows of the table on a page (the core pages a saved report by it; a preview is cut here). */
export const PAGE_SIZE = 500;

export interface PanelState {
  /** `run` — the result of the window; `report` — the saved report opened from the tree */
  mode: "run" | "report";
  reportId: number | null;
  report: ReportResult | null;
  loading: boolean;
  error: string | null;
  /** the template of the form asked of the core; "" — the one saved with the report */
  template: string;
  /** the cut-off of the form: undefined — the first of the report */
  cutoff: number | null | undefined;
  page: number;
  tab: PanelTab;
  element: string | null;
  right: RightAxisKind;
  /** the table shows the rows of this cut-off ("all" — every row) */
  rowCutoff: number | null | "all";
  /** a read that was overtaken does not write its end */
  seq: number;
  /** the last note of an action (a file written, a refusal) */
  note: { text: string; error: boolean } | null;
}

export const INITIAL_PANEL: PanelState = { mode: "run", reportId: null, report: null, loading: false, error: null, template: "", cutoff: undefined, page: 1, tab: "form", element: null, right: "grade", rowCutoff: "all", seq: 0, note: null };

export const panelStore = new Store<PanelState>(INITIAL_PANEL);

/** What the panel shows now: the opened report, else the result of the last run of the window. */
export function shownResult(p: PanelState = panelStore.get()): ReportResult | null {
  return p.mode === "report" ? p.report : resourcesStore.get().run.result;
}

export function resetPanel(): void {
  panelStore.set((p) => ({ ...INITIAL_PANEL, seq: p.seq + 1 }));
}

export const setTab = (tab: PanelTab): void => panelStore.set({ tab });
export const setElement = (element: string | null): void => panelStore.set({ element });
export const setRight = (right: RightAxisKind): void => panelStore.set({ right });
export const setRowCutoff = (rowCutoff: PanelState["rowCutoff"]): void => panelStore.set({ rowCutoff, page: 1 });
export const setNote = (note: PanelState["note"]): void => panelStore.set({ note });

/** The tab is on: the bottom dock opens on it. */
export function showTab(app: App): void {
  app.store.set((s) => ({ workspace: setPanel(s.workspace, "bottom", true, RESOURCES_TAB) }));
}

/** The panel shows the result of the window's last run. */
export function showRun(): void {
  panelStore.set((p) => ({ mode: "run", error: null, loading: false, seq: p.seq + 1, page: 1, ...(p.mode === "run" ? {} : { template: "", cutoff: undefined }) }));
}

interface Ask {
  template?: string;
  cutoff?: number | null | undefined;
  page?: number;
}

/** Reads the saved report `id` (with the template, the cut-off and the page of the panel) and shows it; a new report starts with its own template, cut-off and page. */
export async function openReport(id: number, ask: Ask = {}): Promise<ReportResult | null> {
  const before = panelStore.get();
  const same = before.mode === "report" && before.reportId === id;
  const template = ask.template ?? (same ? before.template : "");
  const cutoff = "cutoff" in ask ? ask.cutoff : same ? before.cutoff : undefined;
  const page = ask.page ?? (same ? before.page : 1);
  const seq = before.seq + 1;
  panelStore.set({ mode: "report", reportId: id, loading: true, error: null, template, cutoff, page, seq, ...(same ? {} : { report: null, rowCutoff: "all", element: null, note: null }) });
  try {
    const report = await loadReport(id, { template: template || undefined, cutoff, page, size: PAGE_SIZE });
    if (panelStore.get().seq !== seq) return null;
    panelStore.set({ report, loading: false });
    return report;
  } catch (e) {
    if (panelStore.get().seq === seq) panelStore.set({ loading: false, error: errorText(e) });
    return null;
  }
}

/** The report the panel shows has changed in the core (rebuilt, renamed): it is read again; deleted — the panel goes back to the run. */
export function reportChanged(id: number, deleted: boolean): void {
  const p = panelStore.get();
  if (p.mode !== "report" || p.reportId !== id) return;
  if (deleted) {
    showRun();
    panelStore.set({ reportId: null, report: null });
    return;
  }
  void openReport(id);
}

/** The id of the saved report the panel shows (the opened one, or the saved result of the window); null — a preview or nothing. */
export function shownReportId(p: PanelState = panelStore.get()): number | null {
  const r = shownResult(p);
  return r && r.saved && r.report_id !== null ? r.report_id : null;
}

/** A template chosen for the form of a saved report (the core makes the cells again). */
export async function chooseTemplate(template: string): Promise<void> {
  const id = shownReportId();
  if (id !== null) await openReport(id, { template, cutoff: undefined, page: panelStore.get().page });
}

/** A cut-off chosen for the form of a saved report. */
export async function chooseCutoff(cutoff: number | null): Promise<void> {
  const id = shownReportId();
  if (id !== null) await openReport(id, { cutoff });
}

/** A page of the rows of the table: a saved report is read again, a preview is cut on the screen. */
export async function choosePage(page: number): Promise<void> {
  const id = shownReportId();
  if (id !== null && panelStore.get().mode === "report") await openReport(id, { page });
  else panelStore.set({ page });
}

/** The rows of the page of the table (a preview has up to 20 000 rows: the page is cut here; a saved report's rows are already the page). */
export function pageRows<T>(rows: readonly T[], p: Pick<PanelState, "mode" | "page">): T[] {
  return p.mode === "report" ? [...rows] : rows.slice((p.page - 1) * PAGE_SIZE, p.page * PAGE_SIZE);
}

export const pageCount = (total: number): number => Math.max(1, Math.ceil(total / PAGE_SIZE));
