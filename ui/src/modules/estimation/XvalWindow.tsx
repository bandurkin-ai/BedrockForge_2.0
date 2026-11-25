/**
 * The window «Перекрёстная проверка» (ТЗ 8.8.5; ADR 0024 §8; docs/progress/e18-api.md §8.1): the row of «Параметры оценки», what to
 * leave out (a hole or a sample), «Выполнить» with the progress and «Отменить» of the job `estimate.xval`; then the summary, the
 * table (the sort, the pages), the scatter plot «истинное — оценка» and the histogram of the errors; «Показать ошибки в 3D» puts the
 * samples into the 3D window coloured by the error. A result out of date says why. The run is the command `estimate.xval`. The window
 * is floating, not modal. The state is in `xvalState.ts`.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { requestClose } from "../../shell/windows";
import { cancelJob, jobsStore } from "../jobs/state";
import { useSel } from "./hooks";
import { estimationStore, rowById } from "./state";
import { BY_HINTS, BY_TITLES, XVAL_BY, type XvalBy } from "./xvalApi";
import { atText, BY_WORDS, COLOR_HIGH, COLOR_LOW, COLOR_NONE, numberText, staleText } from "./xvalModel";
import { errorsShown } from "./xvalScene";
import { hide3d, isPartial, openXval, reason3d, setBy, setTab, staleReasonOf, toggle3d, xvalStore, type XvalTab } from "./xvalState";
import { Errors, Scatter, Summary, Table } from "./XvalParts";
import mod from "./XvalWindow.module.css";

const TABS: readonly { id: XvalTab; title: string }[] = [
  { id: "table", title: "Таблица" },
  { id: "scatter", title: "Рассеяние" },
  { id: "hist", title: "Гистограмма ошибок" },
];

export function XvalWindow({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(xvalStore, (x) => x);
  const list = useSel(estimationStore, (x) => x.rows);
  useSel(estimationStore, (x) => x.details); // the stale words may come from the details of the row
  const jobs = useSel(jobsStore, (x) => x.jobs);
  const readOnly = useSel(app.store, (x) => x.projectReadOnly);
  const [problem, setProblem] = useState<string | null>(null);
  // the window goes — the errors leave the 3D window with it
  useEffect(
    () => () => {
      if (errorsShown()) hide3d();
    },
    [],
  );
  const row = s.estimationId === null ? undefined : rowById(s.estimationId);
  const job = s.job ? jobs.find((j) => j.source === s.job?.source && j.id === s.job?.id) : undefined;
  const busy = s.phase === "running";
  const meta = s.meta;
  const runReason = app.registry.reason("estimate.xval") ?? (readOnly ? "Проект открыт только для чтения" : s.estimationId === null ? "Выберите строку «Параметры оценки»" : row?.source_lost ? "Источник удалён: строку нельзя проверить" : busy ? "Идёт перекрёстная проверка" : null);
  const shownReason = reason3d(s);

  const run = () => s.estimationId !== null && void app.run("estimate.xval", { estimation_id: s.estimationId, by: s.by }).catch(() => undefined); // a refusal stands under the bar
  // the button is the command `estimate.xval_3d` (the same for Ctrl+K and the assistant); a refusal stands under the bar
  const to3d = () => {
    setProblem(null);
    void toggle3d(app, s).then(setProblem);
  };

  return (
    <div className={mod.window} data-testid="estimation-xval">
      <div className={mod.bar}>
        <label>
          <span>Строка «Параметры оценки»</span>
          <select className={mod.select} value={s.estimationId ?? ""} disabled={busy} aria-label="Строка оценки" data-testid="xval-row-select" onChange={(e) => void openXval(e.target.value ? Number(e.target.value) : null)}>
            <option value="">— выберите —</option>
            {list.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Что убирать при проверке</span>
          <select className={mod.select} value={s.by} disabled={busy} aria-label="Что убирать" title={BY_HINTS[s.by]} data-testid="xval-by" onChange={(e) => setBy(e.target.value as XvalBy)}>
            {XVAL_BY.map((b) => (
              <option key={b} value={b}>
                {b === "hole" ? "Скважину целиком" : "Одну пробу"}
              </option>
            ))}
          </select>
        </label>
        <div className={mod.actions}>
          <button type="button" className={css.primary} disabled={runReason !== null} title={runReason ?? `Каждая проба оценивается по остальным (${BY_TITLES[s.by]}); результат заменит прежний`} data-testid="xval-run" onClick={run}>
            Выполнить
          </button>
          {busy && s.job && (
            <button type="button" className={css.btn} title="Отменить: прежний результат останется" data-testid="xval-cancel" onClick={() => s.job && void cancelJob(s.job).catch(() => undefined)}>
              Отменить
            </button>
          )}
        </div>
      </div>
      <div className={mod.main}>
        {s.error && (
          <p className={css.error} role="alert" data-testid="xval-error" data-code={s.error.code}>
            {s.error.message} {s.error.hint}
          </p>
        )}
        {s.note && (
          <p className={css.note} role="status" data-testid="xval-note">
            {s.note}
          </p>
        )}
        {s.estimationId === null && <p className={mod.empty}>Выберите строку «Параметры оценки»: проверка покажет, насколько оценка по соседним пробам совпадает с истинным значением проб.</p>}
        {s.phase === "loading" && !meta && (
          <p className={mod.empty} role="status" data-testid="xval-loading">
            Читаю результат…
          </p>
        )}
        {s.phase === "none" && (
          <p className={mod.empty} data-testid="xval-none">
            Для строки «{row?.name ?? `№${s.estimationId}`}» перекрёстной проверки ещё нет. Выберите, что убирать, и нажмите «Выполнить».
          </p>
        )}
        {meta && (
          <>
            <p className={mod.info} data-testid="xval-info">
              Расчёт {atText(meta.at) ? `от ${atText(meta.at)}` : ""} · {BY_WORDS[meta.by]} · строк в таблице: {meta.total.toLocaleString("ru-RU")}
              {s.reading && ` · читаю таблицу: страница ${s.reading.done} из ${s.reading.of}`}
              {isPartial(s) && !s.reading && ` · прочитаны первые ${s.rows.length.toLocaleString("ru-RU")}: сортировка и график — по ним`}
            </p>
            {meta.stale && (
              <p className={mod.banner} role="status" data-testid="xval-stale">
                Результат устарел. {staleText(meta.at, staleReasonOf(s.estimationId))}
              </p>
            )}
            <Summary meta={meta} />
            <div className={mod.tabs} role="tablist" aria-label="Что показать">
              {TABS.map((t) => (
                <button key={t.id} type="button" role="tab" aria-selected={s.tab === t.id} data-testid={`xval-tab-${t.id}`} onClick={() => setTab(t.id)}>
                  {t.title}
                </button>
              ))}
            </div>
            {s.tab === "table" && <Table />}
            {s.tab === "scatter" && <Scatter meta={meta} />}
            {s.tab === "hist" && <Errors meta={meta} />}
          </>
        )}
      </div>
      <div className={mod.footer}>
        {busy && (
          <div className={mod.progress} data-testid="xval-progress" role="status">
            <progress max={1} value={job?.progress ?? 0} aria-label="Ход проверки" />
            <span className={mod.stage}>{job?.message ?? (job ? "Выполняется" : "Запуск")}</span>
          </div>
        )}
        {problem && (
          <p className={css.error} role="alert" data-testid="xval-3d-error">
            {problem}
          </p>
        )}
        <div className={css.row}>
          <button type="button" className={css.btn} disabled={shownReason !== null && !s.shown} title={s.shown ? "Убрать точки ошибок из 3D-окна" : (shownReason ?? "Проба в её месте, цвет — ошибка: Cobalt — оценка ниже истинного, Copper Orange — выше")} data-testid="xval-3d" onClick={to3d}>
            {s.shown ? "Скрыть ошибки в 3D" : "Показать ошибки в 3D"}
          </button>
          {s.shown && (
            <span className={mod.legend} data-testid="xval-3d-legend">
              <span>−{numberText(s.shown.limit)}</span>
              <span className={mod.ramp} style={{ background: `linear-gradient(to right, ${COLOR_LOW}, ${COLOR_NONE}, ${COLOR_HIGH})` }} aria-hidden="true" />
              <span>+{numberText(s.shown.limit)}</span>
              <span>ошибка (оценка − истинное); проб: {s.shown.n.toLocaleString("ru-RU")}</span>
            </span>
          )}
          <span style={{ flex: 1 }} />
          <button type="button" className={css.btn} data-testid="xval-close" onClick={() => void requestClose(winId)}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
