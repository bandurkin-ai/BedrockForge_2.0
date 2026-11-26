/**
 * The bottom tab «Pit-by-pit» (ТЗ 8.11.4–8.11.5; ADR 0028 решения 18, 19, 29): the report of a scenario that has a result (`pit.report`; a double click on a scenario in the tree) in two
 * tabs — «Таблица» (the text of the core, three views: «По оболочкам», «Выручка и затраты», «Накопленные»; a gap is «—», never 0) and «График» (the ore and the waste by shells, the NPV of the
 * best and of the worst case, the CF) — and the calendar of the view (Q_р, Q_гм, r: `GET report?calendar=`, nothing is written; «Записать в сценарий» is `pit.calendar`). A shell goes to the 3D
 * with its «3D» (`pit.show`). `PitView` is the drawing from what is known (the tests); `PitPanel` is it connected to the stores and to the registry.
 */
import { useMemo, useRef } from "react";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { useApp } from "../../shell/AppContext";
import { useSelect } from "../lineage/hooks";
import { LineageBadge } from "../lineage/LineageBadge";
import { projectStore } from "../projects/state";
import { chartCsv, PitChart } from "./PitChart";
import css from "./PitPanel.module.css";
import { CalendarBar, Legend, ReportTable } from "./PitPanelParts";
import { openReport, pitPanelStore, setMode, setTab, type PanelTab, type PitPanelState } from "./panelState";
import { calendarText, formatNumber, TABLE_MODES, viewTable, type Calendar, type PitReport, type TableMode } from "./reportModel";
import { warningText } from "./format";

export const NODE_KIND = "object:pit_scenario";

export const TAB_TITLES: Record<PanelTab, string> = { table: "Таблица", chart: "График" };

export interface PanelActions {
  tab: (t: PanelTab) => void;
  mode: (m: TableMode) => void;
  calendar: (c: Calendar | null) => void;
  saveCalendar: (c: Calendar) => void;
  show: (k: number) => void;
}

export interface PitViewProps {
  report: PitReport;
  panel: Pick<PitPanelState, "tab" | "mode" | "calendar" | "loading" | "error" | "note">;
  actions: PanelActions;
}

const facts = (r: PitReport): string => {
  const first = r.shells[0];
  const last = r.shells[r.shells.length - 1];
  const rf = first && last ? ` · RF ${formatNumber(first.rf, 2)} … ${formatNumber(last.rf, 2)}` : "";
  return `модель «${r.model.name}» · оболочек ${r.shells.length}${rf} · версия ${r.version} · итог CF ${formatNumber(r.totals.cf, 0)} $`;
};

export function PitView({ report, panel, actions }: PitViewProps) {
  const svg = useRef<SVGSVGElement | null>(null);
  const table = useMemo(() => viewTable(report, panel.mode), [report, panel.mode]);
  const tab = panel.tab;
  const key = `${report.scenario_id}:${JSON.stringify(report.calendar)}`;
  return (
    <div className={css.panel} data-testid="pit-panel" data-scenario={report.scenario_id} data-npv={report.npv_ready ? "ready" : "none"}>
      <div className={css.head}>
        <h4 data-testid="pit-panel-title">Pit-by-pit «{report.name}»</h4>
        <LineageBadge node={{ kind: NODE_KIND, id: report.scenario_id }} />
        <span className={css.facts} data-testid="pit-panel-facts">
          {facts(report)}
        </span>
        <span className={css.grow} />
        {panel.loading && <span className={css.note}>Чтение…</span>}
      </div>
      <CalendarBar key={key} calendar={report.calendar} changed={panel.calendar !== null} busy={panel.loading} onApply={(c) => actions.calendar(c)} onReset={() => actions.calendar(null)} onSave={actions.saveCalendar} />
      {panel.note && (
        <p className={panel.note.error ? css.error : css.note} role={panel.note.error ? "alert" : "status"} data-testid="pit-panel-note">
          {panel.note.text}
        </p>
      )}
      {panel.error && (
        <p className={css.error} role="alert" data-testid="pit-panel-error">
          {panel.error.message} {panel.error.hint}
        </p>
      )}
      {report.warnings.length > 0 && (
        <ul className={css.warns} data-testid="pit-panel-warnings">
          {report.warnings.map((w, i) => (
            <li key={i}>{warningText(w)}</li>
          ))}
        </ul>
      )}
      <div className={css.tabs} role="tablist">
        {(Object.keys(TAB_TITLES) as PanelTab[]).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={t === tab} className={t === tab ? css.tabOn : css.tab} data-testid={`pit-tab-${t}`} onClick={() => actions.tab(t)}>
            {TAB_TITLES[t]}
          </button>
        ))}
      </div>
      <div className={css.body} data-testid={`pit-tab-body-${tab}`}>
        {tab === "table" ? (
          <div className={css.stack} style={{ height: "100%" }}>
            <div className={css.bar}>
              <span className={css.seg} role="group" aria-label="Вид таблицы">
                {TABLE_MODES.map((m) => (
                  <button key={m.mode} type="button" aria-pressed={panel.mode === m.mode} title={m.hint} data-testid={`pit-mode-${m.mode}`} onClick={() => actions.mode(m.mode)}>
                    {m.label}
                  </button>
                ))}
              </span>
              <span className={css.note} data-testid="pit-table-count">
                Оболочек: {report.shells.length} · {calendarText(report.calendar)}
              </span>
            </div>
            {report.shells.length ? <ReportTable table={table} onShow={actions.show} /> : <p className={css.note}>В отчёте нет оболочек.</p>}
          </div>
        ) : (
          <div className={css.chartBox} data-testid="pit-chart" data-points={report.shells.length}>
            <Legend />
            <PitChart ref={svg} report={report} />
            <ChartToolbar svgRef={svg} fileName={`${report.name || "Pit-by-pit"} — график`} csv={() => chartCsv(report)} csvLabel="CSV графика" />
          </div>
        )}
      </div>
    </div>
  );
}

/** The panel in the bottom dock. */
export function PitPanel() {
  const app = useApp();
  const panel = useSelect(pitPanelStore, (s) => s);
  const projectOpen = useSelect(projectStore, (s) => s.current !== null);
  const actions = useMemo<PanelActions>(
    () => ({
      tab: setTab,
      mode: setMode,
      calendar: (c) => {
        const id = pitPanelStore.get().scenarioId;
        if (id !== null) void openReport(id, c);
      },
      saveCalendar: (c) => {
        const id = pitPanelStore.get().scenarioId;
        if (id !== null) void app.run("pit.calendar", { scenario_id: id, q_ore: c.q_ore, q_mass: c.q_mass, rate_pct: c.rate === null ? 8 : Math.round(c.rate * 10000) / 100 });
      },
      show: (k) => {
        const id = pitPanelStore.get().scenarioId;
        if (id !== null) void app.run("pit.show", { scenario_id: id, shell: k });
      },
    }),
    [app],
  );
  if (!projectOpen) return <div className={css.empty}>Откройте проект: сценарии карьера хранятся в проекте.</div>;
  if (!panel.report) {
    return (
      <div className={css.empty} data-testid="pit-panel-empty">
        {panel.loading ? (
          <p className={css.note}>Чтение отчёта…</p>
        ) : panel.error ? (
          <p className={css.error} role="alert">
            {panel.error.message} {panel.error.hint}
          </p>
        ) : (
          <p className={css.note}>Здесь будет pit-by-pit сценария карьера: таблица по оболочкам, график руды, пустой породы, NPV и CF.</p>
        )}
        <button type="button" className={css.btn} data-testid="pit-panel-open" onClick={() => void app.run("pit.report", {})}>
          Показать pit-by-pit выбранного сценария
        </button>
        <span className={css.note}>Сценарии — в разделе «Карьер» → «Сценарии» дерева: двойной щелчок показывает pit-by-pit здесь.</span>
      </div>
    );
  }
  return <PitView report={panel.report} panel={panel} actions={actions} />;
}
