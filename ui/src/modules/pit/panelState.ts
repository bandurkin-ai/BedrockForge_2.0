/**
 * The state of the bottom tab «Pit-by-pit» (ТЗ 8.11.4–8.11.5; ADR 0028 решение 29): the report of the scenario that is shown (`GET /pit/scenarios/{id}/report`), the calendar that
 * the panel asked for (a view: it changes no scenario; none — the calendar of the scenario), the tab («Таблица» / «График»), the mode of the table, the last note. A read that
 * was overtaken (another scenario, another project) does not write its end. No React.
 */
import type { App } from "../../shell/app";
import { setPanel, type BottomTab } from "../../shell/workspace";
import { Store } from "../../shell/store";
import { refusalOf, type Refusal } from "./api";
import { loadReport } from "./reportApi";
import type { Calendar, PitReport, TableMode } from "./reportModel";

/** The id of the tab in the bottom dock (`BottomTab` of the shell gets `pit` — by the orchestrator). */
export const PIT_TAB = "pit" as BottomTab;
export const PIT_PANEL_TITLE = "Pit-by-pit";

export type PanelTab = "table" | "chart";

export interface PitPanelState {
  scenarioId: number | null;
  report: PitReport | null;
  /** the calendar asked by the panel; null — the one of the scenario */
  calendar: Calendar | null;
  loading: boolean;
  error: Refusal | null;
  tab: PanelTab;
  mode: TableMode;
  /** a read that was overtaken does not write its end */
  seq: number;
  note: { text: string; error: boolean } | null;
}

export const INITIAL_PANEL: PitPanelState = { scenarioId: null, report: null, calendar: null, loading: false, error: null, tab: "table", mode: "shells", seq: 0, note: null };

export const pitPanelStore = new Store<PitPanelState>(INITIAL_PANEL);

export const resetPanel = (): void => pitPanelStore.set((p) => ({ ...INITIAL_PANEL, seq: p.seq + 1 }));
export const setTab = (tab: PanelTab): void => pitPanelStore.set({ tab });
export const setMode = (mode: TableMode): void => pitPanelStore.set({ mode });
export const setNote = (note: PitPanelState["note"]): void => pitPanelStore.set({ note });

/** The tab is on: the bottom dock opens on it. */
export function showTab(app: App): void {
  app.store.set((s) => ({ workspace: setPanel(s.workspace, "bottom", true, PIT_TAB) }));
}

/**
 * Reads the report of the scenario `id` and shows it. `calendar`: undefined — the one the panel has for this scenario, null — the calendar of the scenario, a value — this one.
 * A scenario that is another one starts clean (no old table beside the new name). The report, or null (the refusal is `error`).
 */
export async function openReport(id: number, calendar?: Calendar | null): Promise<PitReport | null> {
  const before = pitPanelStore.get();
  const same = before.scenarioId === id;
  const ask = calendar === undefined ? (same ? before.calendar : null) : calendar;
  const seq = before.seq + 1;
  pitPanelStore.set({ scenarioId: id, calendar: ask, loading: true, error: null, seq, ...(same ? {} : { report: null, note: null }) });
  try {
    const report = await loadReport(id, ask);
    if (pitPanelStore.get().seq !== seq) return null;
    pitPanelStore.set({ report, loading: false });
    return report;
  } catch (e) {
    if (pitPanelStore.get().seq === seq) pitPanelStore.set({ loading: false, error: refusalOf(e), ...(same ? {} : { report: null }) });
    return null;
  }
}

/** The scenario the panel shows has changed in the core (run again, renamed): it is read again; deleted — the panel is clean. */
export function scenarioChanged(id: number, deleted: boolean): void {
  const p = pitPanelStore.get();
  if (p.scenarioId !== id) return;
  if (deleted) resetPanel();
  else void openReport(id);
}
