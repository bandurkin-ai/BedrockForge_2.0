/**
 * The subtab «Отчёт» (ТЗ 8.4.3; ADR 0020 §7, §8, §11): «Сформировать» — the name, the elements (all by default)
 * and the charts (all / with warnings and failures / none) with the filter of the tab, through `qaqc.report`; the
 * run «расчёт → графики 37 из 120 → XLSX» with its progress, «Отменить», «Повторить» (from the step it stopped at)
 * and «Сохранить XLSX…»; the reports of the project with their state and «устарел», and of the one chosen — the
 * hashes, the filter, the actions of the tree (`reportActions`): «Сохранить XLSX…», «PDF» (Э22), «Перестроить»,
 * «Переименовать…», «Удалить…». Only own windows.
 */
import { useEffect, useState, useSyncExternalStore } from "react";
import { useApp } from "../../shell/AppContext";
import { errorText } from "../../shell/props";
import jobsCss from "../jobs/Jobs.module.css";
import { jobsStore } from "../jobs/state";
import type { QaqcReport } from "./api";
import { filterSummary, normalizeFilter } from "./filters";
import { int, onActivateKey } from "./format";
import { CHARTS_MODES, CHARTS_TITLES, cancelRun, hasXlsx, loadReports, reportsByName, reportsStore, reportStateTitle, resetRun, retrySpec, runReport, runStore, runText, selectReport, staleTitle, type ChartsMode, type ReportRun } from "./reportRun";
import { reportActions } from "./ReportsTree";
import { activeData, qaqcStore } from "./state";
import css from "./Qaqc.module.css";

/** The share of the run done, 0…1: the job of the step, or the charts sent. */
export function runShare(run: ReportRun, jobProgress: number | null | undefined): number {
  if (run.state === "done") return 1;
  if (run.step === "charts") return run.total ? run.done / run.total : 0;
  return Math.max(0, Math.min(1, jobProgress ?? 0));
}

function RunPanel({ run }: { run: ReportRun }) {
  const app = useApp();
  const get = () => jobsStore.get();
  const jobs = useSyncExternalStore(jobsStore.subscribe, get, get).jobs;
  const live = run.job ? jobs.find((j) => j.source === run.job!.source && j.id === run.job!.id) : undefined;
  const share = runShare(run, live?.progress);
  const [note, setNote] = useState<string | null>(null);
  const save = () => {
    const r = reportsStore.get().list?.find((x) => x.id === run.reportId);
    if (r) void app.run("qaqc.report_save", { report_id: r.id, kind: "xlsx" });
    else setNote("Отчёт не найден в списке: обновите список.");
  };
  return (
    <section className={css.card} aria-label="Формирование отчёта" data-testid="qaqc-report-run" data-state={run.state} data-step={run.step}>
      <h4>
        <span>«{run.spec.name}»</span>
        {run.state !== "running" && (
          <button type="button" className={css.x} aria-label="Закрыть" onClick={resetRun}>
            ×
          </button>
        )}
      </h4>
      <div className={css.steps}>
        {(["calc", "charts", "xlsx"] as const).map((s) => (
          <span key={s} className={run.step === s ? css.stepOn : undefined}>
            {s === "calc" ? "Расчёт" : s === "charts" ? (run.spec.charts === "none" ? "Графики: нет" : `Графики ${int(run.done)} из ${int(run.total)}`) : "XLSX"}
          </span>
        ))}
      </div>
      <div data-testid="qaqc-report-status">
        {runText(run)}
        {run.state === "running" && run.step !== "charts" && live?.message ? ` · ${live.message}` : ""}
      </div>
      {run.state === "running" && (
        <div className={jobsCss.progress}>
          <div style={{ width: `${Math.round(share * 100)}%` }} />
        </div>
      )}
      {run.omitted > 0 && <p className={css.note}>Графиков сверх 200: {int(run.omitted)} — строка в «Сводке» отчёта.</p>}
      {run.empty > 0 && <p className={css.note}>Без рисунка (нечего рисовать): {int(run.empty)}.</p>}
      <div className={css.row}>
        {run.state === "running" && (
          <button type="button" className={css.btn} disabled={run.cancelling} onClick={() => void cancelRun()}>
            Отменить
          </button>
        )}
        {(run.state === "failed" || run.state === "cancelled") && (
          <button type="button" className={css.primary} onClick={() => void runReport(retrySpec(run)).catch((e: unknown) => setNote(errorText(e)))}>
            Повторить
          </button>
        )}
        {run.state === "done" && run.reportId !== null && !run.path && (
          <button type="button" className={css.primary} onClick={save}>
            Сохранить XLSX…
          </button>
        )}
      </div>
      {note && <p className={css.note}>{note}</p>}
    </section>
  );
}

function CreateForm({ busy }: { busy: boolean }) {
  const app = useApp();
  const s = useSyncExternalStore(qaqcStore.subscribe, qaqcStore.get, qaqcStore.get);
  const data = activeData(s);
  const [name, setName] = useState("");
  const [off, setOff] = useState<string[]>([]);
  const [charts, setCharts] = useState<ChartsMode>("flagged");
  if (!data) return null;
  const elements = data.elements.map((e) => e.name).filter((e) => !off.includes(e));
  const reason = busy ? "Отчёт уже формируется" : !elements.length ? "Выберите хотя бы один элемент" : app.registry.reason("qaqc.report");
  const start = () =>
    void app.run("qaqc.report", {
      data_id: data.id,
      ...(name.trim() ? { name: name.trim() } : {}),
      ...(elements.length < data.elements.length ? { elements: JSON.stringify(elements) } : {}),
      charts,
    });
  return (
    <fieldset className={css.group} aria-label="Сформировать отчёт">
      <legend>Сформировать отчёт</legend>
      <label className={css.field}>
        <span>Название</span>
        <input value={name} placeholder={`Отчёт QAQC — ${data.name}`} onChange={(e) => setName(e.target.value)} />
      </label>
      <div className={css.field}>
        <span>Элементы</span>
        <div className={css.checks}>
          {data.elements.map((e) => (
            <label key={e.name} className={css.check}>
              <input type="checkbox" checked={!off.includes(e.name)} onChange={(ev) => setOff((o) => (ev.target.checked ? o.filter((x) => x !== e.name) : [...o, e.name]))} />
              {e.name}
            </label>
          ))}
        </div>
      </div>
      <div className={css.field}>
        <span>Графики</span>
        <span className={css.seg} role="group" aria-label="Графики отчёта">
          {CHARTS_MODES.map((m) => (
            <button key={m} type="button" aria-pressed={charts === m} onClick={() => setCharts(m)}>
              {CHARTS_TITLES[m]}
            </button>
          ))}
        </span>
      </div>
      <p className={css.note}>
        Данные «{data.name}», фильтр вкладки: {filterSummary(s.filter)}.
      </p>
      <div className={css.row}>
        <button type="button" className={css.primary} disabled={!!reason} title={reason ?? undefined} onClick={start}>
          Сформировать
        </button>
        <button type="button" className={css.btn} disabled title="Появится на этапе Э22">
          PDF
        </button>
      </div>
    </fieldset>
  );
}

function ReportCard({ report }: { report: QaqcReport }) {
  const app = useApp();
  const run = (id: string, args: Record<string, unknown>) => void app.run(id, args);
  const actions = reportActions(report, (id) => app.registry.reason(id), run).filter((a) => a.label !== "Открыть");
  const stale = staleTitle(report.stale);
  return (
    <section className={css.card} aria-label="Отчёт QAQC" data-testid="qaqc-report-card">
      <h4>
        <span>«{report.name}» · версия {report.version}</span>
        <button type="button" className={css.x} aria-label="Закрыть" onClick={() => selectReport(null)}>
          ×
        </button>
      </h4>
      {stale && <p className={css.stale}>{stale}</p>}
      <table className={css.facts}>
        <tbody>
          {(
            [
              ["Состояние", reportStateTitle(report.state)],
              ["Данные", report.data_name],
              ["Элементы", report.elements.join(", ") || "—"],
              ["Фильтр", filterSummary(normalizeFilter(report.filter))],
              ["Графики", report.charts ? CHARTS_TITLES[report.charts] : "—"],
              ["Рисунков", int(report.images)],
              ["Файл XLSX", hasXlsx(report) ? "записан" : "нет"],
              ["SHA-256 записей", report.data_sha256],
              ["SHA-256 входов", report.inputs_sha256],
            ] as const
          ).map(([k, v]) => (
            <tr key={k}>
              <th>{k}</th>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className={css.row}>
        {actions.map((a) => (
          <button key={a.label} type="button" className={css.btn} disabled={!!a.reason || !a.run} title={a.reason ?? undefined} onClick={a.run}>
            {a.label}
          </button>
        ))}
      </div>
    </section>
  );
}

export function ReportTab() {
  const get = () => reportsStore.get();
  const { list, error, selected } = useSyncExternalStore(reportsStore.subscribe, get, get);
  const getRun = () => runStore.get();
  const { run } = useSyncExternalStore(runStore.subscribe, getRun, getRun);
  useEffect(() => {
    if (list === null && !error) void loadReports();
  }, [list, error]);
  const chosen = list?.find((r) => r.id === selected) ?? null;
  return (
    <div className={css.split} data-testid="qaqc-report">
      <div className={css.reportSide}>
        <CreateForm busy={run?.state === "running"} />
        {run && <RunPanel run={run} />}
      </div>
      <div className={css.tableBox}>
        {error && <p className={css.error}>{error}</p>}
        {list === null ? (
          !error && <p className={css.note}>Чтение отчётов…</p>
        ) : !list.length ? (
          <p className={css.note}>Отчётов QAQC в проекте нет.</p>
        ) : (
          <table className={css.grid} aria-label="Отчёты QAQC">
            <thead>
              <tr>
                {["Отчёт", "Данные", "Элементы", "Состояние", "Устарел", "Рисунков", "XLSX"].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {reportsByName(list).map((r) => (
                <tr key={r.id} className={r.id === selected ? css.chosen : undefined} data-report={r.id} tabIndex={0} aria-selected={r.id === selected} onClick={() => selectReport(r.id)} onKeyDown={onActivateKey(() => selectReport(r.id))}>
                  <th>{r.name}</th>
                  <td style={{ textAlign: "left" }}>{r.data_name}</td>
                  <td style={{ textAlign: "left" }}>{r.elements.join(", ")}</td>
                  <td className={r.state === "failed" ? css.fail : undefined}>{reportStateTitle(r.state)}</td>
                  <td className={r.stale ? css.warn : undefined}>{r.stale ? staleTitle(r.stale).replace(/^устарел: /, "") : "—"}</td>
                  <td>{int(r.images)}</td>
                  <td>{hasXlsx(r) ? "да" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {chosen && <ReportCard report={chosen} />}
      </div>
    </div>
  );
}

