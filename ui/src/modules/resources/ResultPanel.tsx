/**
 * The bottom tab «Ресурсы» (ТЗ 8.10 «Вывод», «Интерфейс»; ADR 0027 решения 16, 19, 29): the result of the window «Отчёт по ресурсам» (the preview or the saved report) or a saved
 * report opened from the tree (`resources.open`) in four blocks — «Форма» (the form of the template: the cells are the core's text, shown as they are), «Таблица» (all the
 * rows: group, cut-off, blocks, volume, tonnes, grades, metals), «Кривая» (T, ḡ, M against the cut-off) and «Контроль» (the sum of the groups, the blocks not counted, the
 * statuses of the estimate, the wireframes, the warnings). The template and the cut-off of the form are chosen here (the core makes the form again: a saved report only),
 * XLSX and CSV are saved from here (`resources.export`). A click on a cell of a saved report goes to `emitCellClick` (20.15). `ResultView` is the drawing from what is known
 * (the tests); `ResultPanel` is it connected to the stores and to the registry.
 */
import { useCallback, useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { LineageBadge } from "../lineage/LineageBadge";
import type { Form, ReportResult, TemplateRow } from "./answers";
import { emitCellClick, type CellRef } from "./cells";
import { ControlView, allWarnings } from "./ControlView";
import { CurveChart } from "./CurveChart";
import { saveReportFile, type ExportFormat, type ExportWhat } from "./exportFile";
import { FormTable, RowsTable, type CellHandler } from "./FormTable";
import { fmtCount, fmtCutoff } from "./format";
import css from "./Panel.module.css";
import {
  PAGE_SIZE,
  chooseCutoff,
  choosePage,
  chooseTemplate,
  pageCount,
  pageRows,
  panelStore,
  setElement,
  setNote,
  setRight,
  setRowCutoff,
  setTab,
  shownReportId,
  shownResult,
  type PanelState,
  type PanelTab,
} from "./panelState";
import { resourcesStore } from "./state";
import { useSel } from "./ReportParts";

export const TAB_TITLES: Record<PanelTab, string> = { form: "Форма", table: "Таблица", curve: "Кривая", control: "Контроль" };

export interface PanelActions {
  tab: (t: PanelTab) => void;
  template: (id: string) => void;
  cutoff: (c: number | null) => void;
  page: (p: number) => void;
  element: (name: string) => void;
  right: (r: "grade" | "metal") => void;
  rowCutoff: (c: PanelState["rowCutoff"]) => void;
  exportFile: (format: ExportFormat, what?: ExportWhat) => void;
}

export interface ResultViewProps {
  result: ReportResult;
  panel: PanelState;
  templates: readonly TemplateRow[];
  actions: PanelActions;
  /** a file is being written */
  busy?: boolean;
  onCell?: CellHandler;
}

/**
 * The cut-offs the form and the table can show: those of the form, else the distinct ones of the rows. The core lists only the numeric cut-offs of the form
 * (`kind` cutoff): «без борта» (the rows of kind `all`, or a form that is built without a cut-off) is `null`, first — else a form on «без борта» could not be left back.
 */
export function cutoffOptions(result: ReportResult): (number | null)[] {
  const fromForm = result.form?.cutoffs ?? [];
  const set = new Map<string, number | null>();
  for (const c of fromForm) set.set(String(c), c);
  if (!set.size) for (const r of result.rows) if (r.kind === "cutoff" || r.kind === "all") set.set(String(r.cutoff), r.cutoff);
  const none = result.form?.cutoff === null || result.rows.some((r) => r.kind === "all");
  const list = [...set.values()].filter((c) => c !== null);
  return none || set.has("null") ? [null, ...list] : list;
}

const cutoffLabel = (c: number | null): string => (c === null ? "без борта" : `≥ ${fmtCutoff(c)}`);

function Choosers({ result, panel, templates, actions }: Pick<ResultViewProps, "result" | "panel" | "templates" | "actions">) {
  const saved = result.saved && result.report_id !== null;
  const form: Form | null = result.form;
  const tpl = panel.template || form?.template_id || "";
  const cuts = cutoffOptions(result);
  const formCut = panel.cutoff !== undefined ? panel.cutoff : (form?.cutoff ?? null);
  const why = "Предпросмотр не записан в проект: форма построена для шаблона и борта окна. Сохраните отчёт, чтобы выбирать шаблон и борт здесь";
  return (
    <>
      <label title={saved ? "Форма отчёта: ядро строит её по шаблону, интерфейс показывает её текст как есть" : why}>
        Шаблон
        <select value={tpl} disabled={!saved} aria-label="Шаблон формы" data-testid="res-template" onChange={(e) => actions.template(e.target.value)}>
          {!tpl && <option value="">— без формы —</option>}
          {tpl && !templates.some((t) => t.id === tpl) && <option value={tpl}>{form?.template_name || tpl}</option>}
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <label title={saved ? "Борт формы: строки отчёта с этим бортом" : why}>
        Борт формы
        <select value={cuts.findIndex((c) => c === formCut)} disabled={!saved || !form || cuts.length < 2 || !!form.columns.some((c) => c.part)} aria-label="Борт формы" data-testid="res-form-cutoff" onChange={(e) => actions.cutoff(cuts[Number(e.target.value)] ?? null)}>
          {cuts.map((c, i) => (
            <option key={i} value={i}>
              {cutoffLabel(c)}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

function TableTab({ result, panel, actions, onCell }: Pick<ResultViewProps, "result" | "panel" | "actions" | "onCell">) {
  const cuts = cutoffOptions(result);
  const all = pageRows(result.rows, panel);
  const rows = panel.rowCutoff === "all" ? all : all.filter((r) => (r.kind === "cutoff" || r.kind === "all") && r.cutoff === panel.rowCutoff);
  const total = panel.mode === "report" ? result.rows_total : result.rows.length;
  const pages = pageCount(total);
  return (
    <div className={css.stack} style={{ height: "100%", minHeight: 0 }}>
      <div className={css.bar}>
        <label>
          Борт
          <select value={panel.rowCutoff === "all" ? "all" : String(cuts.findIndex((c) => c === panel.rowCutoff))} aria-label="Борт таблицы" data-testid="res-table-cutoff" onChange={(e) => actions.rowCutoff(e.target.value === "all" ? "all" : (cuts[Number(e.target.value)] ?? null))}>
            <option value="all">все строки</option>
            {cuts.map((c, i) => (
              <option key={i} value={i}>
                {cutoffLabel(c)}
              </option>
            ))}
          </select>
        </label>
        <span className={css.facts} data-testid="res-table-count">
          Строк: {fmtCount(rows.length)}
          {rows.length !== total ? ` из ${fmtCount(total)}` : ""}
          {result.truncated ? " (предпросмотр показывает не все: сохраните отчёт)" : ""}
        </span>
        {pages > 1 && (
          <span className={css.pager} data-testid="res-pager">
            <button type="button" className={css.btn} disabled={panel.page <= 1} aria-label="Предыдущая страница" onClick={() => actions.page(panel.page - 1)}>
              ‹
            </button>
            Страница {panel.page} из {pages} (по {PAGE_SIZE})
            <button type="button" className={css.btn} disabled={panel.page >= pages} aria-label="Следующая страница" onClick={() => actions.page(panel.page + 1)}>
              ›
            </button>
          </span>
        )}
      </div>
      <div className={css.body}>
        {rows.length ? <RowsTable result={result} rows={rows} onCell={onCell} /> : <p className={css.note}>В таблице нет строк{panel.rowCutoff === "all" ? "" : " с этим бортом на этой странице"}.</p>}
      </div>
    </div>
  );
}

export function ResultView({ result, panel, templates, actions, busy, onCell }: ResultViewProps) {
  const saved = result.saved && result.report_id !== null;
  const warnings = allWarnings(result);
  const form = result.form;
  const tab = panel.tab;
  return (
    <div className={css.panel} data-testid="res-panel" data-mode={panel.mode} data-saved={saved || undefined}>
      <div className={css.head}>
        <h4 data-testid="res-panel-title">{saved ? `Отчёт «${result.name}» №${result.report_id}` : "Предпросмотр отчёта по ресурсам"}</h4>
        {saved && result.report_id !== null && <LineageBadge node={{ kind: "object:resource_report", id: result.report_id }} />}
        <span className={css.facts}>
          модель «{result.model.name}» · поле борта {result.cutoff_field} · строк {fmtCount(result.rows_total)}
          {saved ? ` · версия ${result.version}` : " · не записан в проект"}
          {warnings.length ? ` · предупреждений ${warnings.length}` : ""}
        </span>
        <span className={css.grow} />
        {panel.loading && <span className={css.note}>Чтение…</span>}
      </div>
      <div className={css.bar}>
        <Choosers result={result} panel={panel} templates={templates} actions={actions} />
        <span className={css.grow} />
        <button type="button" className={css.btn} disabled={!saved || busy} title={saved ? "Книга XLSX: листы «Отчёт» (форма), «Таблица», «Кривая», «Контроль», «Параметры»" : "Сохраните отчёт в проект: экспорт идёт из записанного отчёта"} data-testid="res-export-xlsx" onClick={() => actions.exportFile("xlsx")}>
          Сохранить XLSX…
        </button>
        <button type="button" className={css.btn} disabled={!saved || busy} title={saved ? "CSV таблицы строк: «;», десятичная запятая" : "Сохраните отчёт в проект: экспорт идёт из записанного отчёта"} data-testid="res-export-csv" onClick={() => actions.exportFile("csv", "table")}>
          CSV таблицы…
        </button>
        <button type="button" className={css.btn} disabled={!saved || busy || !form} title={saved ? "CSV формы шаблона: те же числа, что в таблице формы и в XLSX" : "Сохраните отчёт в проект: экспорт идёт из записанного отчёта"} data-testid="res-export-form" onClick={() => actions.exportFile("csv", "form")}>
          CSV формы…
        </button>
      </div>
      {panel.note && (
        <p className={panel.note.error ? css.error : css.note} role={panel.note.error ? "alert" : "status"} data-testid="res-panel-note">
          {panel.note.text}
        </p>
      )}
      {panel.error && (
        <p className={css.error} role="alert" data-testid="res-panel-error">
          {panel.error}
        </p>
      )}
      <div className={css.tabs} role="tablist">
        {(Object.keys(TAB_TITLES) as PanelTab[]).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={t === tab} className={t === tab ? css.tabOn : css.tab} data-testid={`res-tab-${t}`} onClick={() => actions.tab(t)}>
            {TAB_TITLES[t]}
          </button>
        ))}
      </div>
      <div className={css.body} data-testid={`res-tab-body-${tab}`}>
        {tab === "form" &&
          (form ? (
            <>
              {form.warnings.length > 0 && (
                <ul className={css.warns} data-testid="res-form-warnings">
                  {form.warnings.map((w, i) => (
                    <li key={i}>{w.hint ? `${w.message} ${w.hint}` : w.message}</li>
                  ))}
                </ul>
              )}
              <FormTable result={result} form={form} onCell={onCell} />
            </>
          ) : (
            <p className={css.note} data-testid="res-form-none">
              У отчёта нет формы: {saved ? "выберите шаблон (JORC, ГКЗ или свой) в списке «Шаблон»." : "выберите шаблон в окне «Отчёт по ресурсам» и постройте предпросмотр заново."}
            </p>
          ))}
        {tab === "table" && <TableTab result={result} panel={panel} actions={actions} onCell={onCell} />}
        {tab === "curve" &&
          (result.curve ? (
            <CurveChart result={result} curve={result.curve} element={panel.element} right={panel.right} onElement={actions.element} onRight={actions.right} />
          ) : (
            <p className={css.note} data-testid="res-curve-none">
              Кривая не построена: «Точек кривой» — 0 или в поле борта нет значений.
            </p>
          ))}
        {tab === "control" && <ControlView result={result} />}
      </div>
    </div>
  );
}

/** The panel in the bottom dock. */
export function ResultPanel({ onCellClick }: { onCellClick?: (cell: CellRef) => void }) {
  const app = useApp();
  const panel = useSel(panelStore, (s) => s);
  useSel(resourcesStore, (s) => s.run); // the result of the window is what the panel shows in the mode «run»
  const templates = useSel(resourcesStore, (s) => s.templates);
  const projectOpen = useSel(app.store, (s) => s.projectName !== null);
  const [busy, setBusy] = useState(false);
  const result = shownResult(panel);
  const click = useCallback<CellHandler>((c) => (onCellClick ?? emitCellClick)(c), [onCellClick]);
  const actions = useMemo<PanelActions>(
    () => ({
      tab: setTab,
      template: (id) => void chooseTemplate(id),
      cutoff: (c) => void chooseCutoff(c),
      page: (p) => void choosePage(p),
      element: setElement,
      right: setRight,
      rowCutoff: setRowCutoff,
      exportFile: (format, what) => {
        const id = shownReportId();
        const r = shownResult();
        if (id === null || !r) return;
        const p = panelStore.get();
        setBusy(true);
        setNote(null);
        void saveReportFile(app, { id, name: r.name, format, what, template: p.template, cutoff: p.cutoff })
          .then((note) => setNote(note))
          .finally(() => setBusy(false));
      },
    }),
    [app],
  );
  if (!projectOpen) return <div className={css.empty}>Откройте проект: отчёты по ресурсам хранятся в проекте.</div>;
  if (!result) {
    return (
      <div className={css.empty} data-testid="res-panel-empty">
        {panel.loading ? <p className={css.note}>Чтение отчёта…</p> : panel.error ? <p className={css.error}>{panel.error}</p> : <p className={css.note}>Здесь будет результат подсчёта ресурсов: форма отчёта, таблица, кривая и контроль.</p>}
        <button type="button" className={css.btn} data-testid="res-panel-open-window" onClick={() => void app.run("resources.report", {})}>
          Открыть окно «Отчёт по ресурсам»
        </button>
        <span className={css.note}>Записанные отчёты — в разделе «Отчёты» → «Отчёты по ресурсам» дерева: двойной щелчок показывает отчёт здесь.</span>
      </div>
    );
  }
  return <ResultView result={result} panel={panel} templates={templates} actions={actions} busy={busy} onCell={click} />;
}
