/**
 * «Настройка QAQC → Данные» (ADR 0020 §2, §11): the list of the data objects (records, elements, dates, source,
 * «устарели» of a snapshot of a set's table with «Обновить»), loading from a file or from a table of a set (the
 * form), the import job with its progress and «Отменить», the totals with the counters of the import, renaming,
 * replacing and deleting. Every action runs its command (`qaqc.import`, `qaqc.data_*`, `qaqc.open`).
 */
import { useApp } from "../../../shell/AppContext";
import { askText } from "../../../shell/dialogs";
import { useSnap } from "./useSnap";
import css from "../../../shell/PropsParts.module.css";
import jobsCss from "../../jobs/Jobs.module.css";
import { cancelJob, jobsStore, STATUS_TITLES } from "../../jobs/state";
import { tablesStore } from "../../tables/state";
import type { QaqcData } from "../setupApi";
import { dataByName, dataStore, dismissRun, openForm, type ImportDone, type ImportRun } from "./dataStore";
import { ImportForm } from "./ImportForm";
import { countLines } from "./mapping";
import own from "./Setup.module.css";

/** «12.03.2024 — 30.06.2024»; no dates — «—». */
export function datesText(d: Pick<QaqcData, "date_from" | "date_to">): string {
  const f = (s: string | null | undefined) => (s ? s.slice(0, 10).split("-").reverse().join(".") : "");
  const a = f(d.date_from);
  const b = f(d.date_to);
  return a || b ? `${a || "…"} — ${b || "…"}` : "—";
}

export function sourceText(d: Pick<QaqcData, "source">): string {
  const s = d.source;
  if (!s) return "—";
  if (s.kind === "table") return `таблица набора${s.name ? ` «${s.name}»` : ""}${s.rev !== undefined ? `, ревизия ${s.rev}` : ""}`;
  return `файл${s.name ? ` «${s.name}»` : ""}`;
}

export const elementsText = (d: Pick<QaqcData, "elements">): string => d.elements.map((e) => (e.unit ? `${e.name}, ${e.unit}` : e.name)).join("; ") || "—";

/** Unrecognised codes of TYPE of the data (they go to «Настройка → Типы»). */
export const unknownCodes = (d: Pick<QaqcData, "type_codes">): number => (d.type_codes ?? []).filter((c) => c.type === null).reduce((n, c) => n + c.n, 0);

function JobPanel({ run, done }: { run: ImportRun; done: ImportDone | null }) {
  const app = useApp();
  const live = useSnap(jobsStore, (s) => s.jobs.find((j) => j.source === run.job.source && j.id === run.job.id));
  const status = live?.status ?? "queued";
  const finished = status === "done" || status === "failed" || status === "cancelled";
  const pct = Math.round((live?.progress ?? 0) * 100);
  return (
    <div className={css.win} data-testid="qaqc-import-job">
      <div className={css.winBody}>
        <h4 className={css.name}>
          Загрузка «{run.name}» · задание №{run.job.id}
        </h4>
        <div data-testid="qaqc-import-status" data-status={status}>
          {live?.cancel_requested && status === "running" ? "Отмена…" : STATUS_TITLES[status]}
          {status === "running" && ` · ${pct}%${live?.message ? ` · ${live.message}` : ""}`}
        </div>
        {!finished && (
          <div className={jobsCss.progress}>
            <div style={{ width: `${pct}%` }} />
          </div>
        )}
        {status === "queued" && live?.why_waiting && <p className={css.note}>{live.why_waiting}</p>}
        {status === "failed" && (
          <p className={css.error} role="alert">
            {live?.error ? `${live.error.message} ${live.error.hint}`.trim() : "Загрузка завершилась ошибкой."}
          </p>
        )}
        {status === "cancelled" && <p className={css.note}>Загрузка отменена: ничего не записано.</p>}
        {done && (
          <ul className={own.counts} data-testid="qaqc-import-counts">
            {countLines(done.rows, done.counts).map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        )}
      </div>
      <div className={css.footer}>
        <span className={css.grow} />
        {!finished && (
          <button type="button" className={css.btn} disabled={live?.cancel_requested} onClick={() => void cancelJob(run.job).catch(() => undefined)}>
            {live?.cancel_requested ? "Отмена…" : "Отменить"}
          </button>
        )}
        {done?.dataId != null && (
          <button type="button" className={css.btn} onClick={() => void app.run("qaqc.open", { section: "standards", data_id: done.dataId })}>
            Открыть во вкладке «QAQC»
          </button>
        )}
        {finished && (
          <button type="button" className={css.primary} onClick={dismissRun}>
            К списку
          </button>
        )}
      </div>
    </div>
  );
}

/** The buttons of one data object: each runs its command; an unavailable one says why. */
export function dataActions(d: QaqcData, reason: (id: string) => string | null, run: (id: string, args: Record<string, unknown>) => void) {
  return [
    { label: "Открыть", reason: reason("qaqc.open"), run: () => run("qaqc.open", { section: "standards", data_id: d.id }) },
    ...(d.source?.kind === "table" || d.stale ? [{ label: "Обновить", reason: reason("qaqc.data_refresh"), run: () => run("qaqc.data_refresh", { data_id: d.id }) }] : []),
    {
      label: "Переименовать…",
      reason: reason("qaqc.data_rename"),
      run: () =>
        void askText({ title: "Переименовать данные QAQC", label: "Название", initial: d.name, okLabel: "Переименовать" }).then((name) => {
          if (name && name.trim() && name.trim() !== d.name) run("qaqc.data_rename", { data_id: d.id, name: name.trim() });
        }),
    },
    { label: "Заменить из файла…", reason: reason("qaqc.import"), run: () => openForm({ mode: "file", replaceId: d.id }) },
    { label: "Удалить…", reason: reason("qaqc.data_delete"), run: () => run("qaqc.data_delete", { data_id: d.id }) },
  ];
}

export function DataList({ list, reason, run }: { list: readonly QaqcData[]; reason: (id: string) => string | null; run: (id: string, args: Record<string, unknown>) => void }) {
  if (!list.length) return <p className={css.note}>Контрольных проб в проекте нет: загрузите их из файла или из таблицы проб набора.</p>;
  return (
    <div className={css.tableWrap} style={{ maxHeight: "none" }}>
      <table className={css.table} data-testid="qaqc-data-list">
        <thead>
          <tr>
            <th>Название</th>
            <th>Записей</th>
            <th>Элементы</th>
            <th>Даты</th>
            <th>Источник</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {dataByName(list).map((d) => {
            const unknown = unknownCodes(d);
            return (
              <tr key={d.id} data-testid={`qaqc-data-${d.id}`}>
                <td title={`данные QAQC №${d.id}`}>
                  {d.name}
                  {d.stale && (
                    <span className={own.stale} title="Таблица набора изменилась после загрузки: «Обновить» загрузит её заново">
                      {" "}
                      · устарели
                    </span>
                  )}
                  {unknown > 0 && <span className={css.warn} title="Коды TYPE не распознаны: «Типы»"> · не распознано: {unknown.toLocaleString("ru-RU")}</span>}
                </td>
                <td className={own.num}>{d.rows.toLocaleString("ru-RU")}</td>
                <td>{elementsText(d)}</td>
                <td>{datesText(d)}</td>
                <td>{sourceText(d)}</td>
                <td>
                  <span className={css.inline}>
                    {dataActions(d, reason, run).map((a) => (
                      <button key={a.label} type="button" className={css.btn} disabled={a.reason !== null} title={a.reason ?? undefined} onClick={a.run} data-testid={`qaqc-data-${d.id}-${a.label.replace(/[…\s]+.*$/, "")}`}>
                        {a.label}
                      </button>
                    ))}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function DataTab() {
  const app = useApp();
  const s = useSnap(dataStore, (x) => x);
  const run = (id: string, args: Record<string, unknown>) => void app.run(id, args);
  const reason = (id: string) => app.registry.reason(id);
  if (s.form) return <ImportForm key={s.form.seq} request={s.form} list={s.list} />;
  if (s.run) return <JobPanel run={s.run} done={s.done} />;
  const activeSet = tablesStore.get().setId;
  return (
    <div className={css.win} data-testid="qaqc-data-tab">
      <div className={css.winBody}>
        <div className={own.bar}>
          <button type="button" className={css.primary} disabled={reason("qaqc.import") !== null} title={reason("qaqc.import") ?? undefined} onClick={() => run("qaqc.import", {})}>
            Загрузить из файла…
          </button>
          <button type="button" className={css.btn} disabled={reason("qaqc.import") !== null} title={reason("qaqc.import") ?? undefined} onClick={() => openForm({ mode: "table", setId: activeSet })}>
            Из таблицы набора…
          </button>
          <span className={own.grow} />
          {!s.loaded && <span className={css.note}>Чтение…</span>}
        </div>
        {s.error && <p className={css.error}>{s.error}</p>}
        <DataList list={s.list} reason={reason} run={run} />
      </div>
    </div>
  );
}
