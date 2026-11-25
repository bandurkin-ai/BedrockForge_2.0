/**
 * The window «Глобальная проверка оценки» (ТЗ 8.8.5; docs/progress/e18-api.md §8.3–§8.4): the means of the composites of the domain (naive
 * and declustered), of the nearest sample and of the estimate on the same blocks, the difference of the estimate from the declustered mean
 * against the norm of 5 % (green or red of the brand, always with words), the variance of the blocks against the expected one (C̄_VV), the
 * warnings of the core, the Q-Q of the composites against the blocks, «Swath…» (the window of Э13 opens with the settings of the core).
 * Floating, not modal; the check writes nothing, so it runs on a project open for reading too.
 */
import { useState } from "react";
import { QQChart } from "../../charts/qq/QQChart";
import { qqMarks, qqPoints } from "../../charts/qq/model";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { requestClose } from "../../shell/windows";
import { jobsStore } from "../jobs/state";
import type { BlockMoments, GlobalResult, QqAnswer } from "./checksApi";
import mod from "./Checks.module.css";
import { cancelGlobal, checkedRows, globalReason, globalStore, blocksText, ratioHint, referenceText, runGlobal, setGlobalRow, setWeighted, statRows, varianceText, verdictOf, verdictText } from "./globalState";
import { useSel } from "./hooks";
import { num } from "./knaState";
import { estimationStore } from "./state";

const BRAND_BLUE = "#1f6bff";

function Verdict({ title, diff, norm, limit, id }: { title: string; diff: number | null; norm: boolean | null; limit: number; id: string }) {
  const v = verdictOf(diff, norm);
  return (
    <div className={mod.verdict} data-testid={id} data-verdict={v}>
      <span>{title}</span>
      <span className={`${mod.badge} ${id === "global-verdict" ? mod.big : ""}`} data-verdict={v}>
        {verdictText(diff, norm, limit)}
      </span>
    </div>
  );
}

function Summary({ g }: { g: GlobalResult }) {
  const hint = ratioHint(g);
  return (
    <>
      <Verdict id="global-verdict" title={`Среднее оценки против ${referenceText(g)}:`} diff={g.diff_pct} norm={g.norm} limit={g.limit_pct} />
      <Verdict id="global-verdict-nn" title="Справочно, оценка против ближайшего соседа (NN) на тех же блоках:" diff={g.diff_nn_pct} norm={g.norm_nn} limit={g.limit_pct} />
      <div className={mod.tableWrap}>
        <table className={mod.table} data-testid="global-table">
          <thead>
            <tr>
              <th>{g.field}</th>
              <th>Число</th>
              <th title="Объём блоков, м³">Объём, м³</th>
              <th>Среднее</th>
              <th title="Дисперсия, делитель — сумма весов">Дисперсия</th>
              <th title="Коэффициент вариации">CV</th>
            </tr>
          </thead>
          <tbody>
            {statRows(g).map((r) => (
              <tr key={r.key} data-testid={`global-row-${r.key}`}>
                <td>{r.title}</td>
                <td>{r.n.toLocaleString("ru-RU")}</td>
                <td>{r.volume === null ? "—" : r.volume.toLocaleString("ru-RU", { maximumFractionDigits: 0 })}</td>
                <td>{num(r.mean, 4)}</td>
                <td>{num(r.var, 4)}</td>
                <td>{num(r.cv, 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <section className={mod.panel} data-testid="global-variance">
        <h3>Дисперсия блоков</h3>
        <p>{varianceText(g)}</p>
        {hint && <p className={css.note}>{hint}</p>}
      </section>
      <p className={css.note} data-testid="global-blocks">
        {blocksText(g)}
      </p>
      {g.warnings.length > 0 && (
        <ul className={mod.notes} data-testid="global-warnings">
          {g.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
    </>
  );
}

const side = (title: string, m: { n: number; mean: number | null; var: number | null; cv: number | null } | BlockMoments | null) => (
  <tr>
    <td>{title}</td>
    <td>{m ? m.n.toLocaleString("ru-RU") : "—"}</td>
    <td>{num(m?.mean ?? null, 4)}</td>
    <td>{num(m?.var ?? null, 4)}</td>
    <td>{num(m?.cv ?? null, 3)}</td>
  </tr>
);

function QqPart({ q, busy, weighted }: { q: QqAnswer | null; busy: boolean; weighted: boolean }) {
  const [log, setLog] = useState(false);
  const [equal, setEqual] = useState(true);
  const s = useSel(globalStore, (x) => x);
  const pts = q ? qqPoints({ points: q.points }, log) : [];
  const marks = q ? qqMarks({ marks: q.marks }, pts, log) : [];
  return (
    <section data-testid="global-qq" data-phase={s.qqPhase}>
      <h3>Q-Q: композиты домена против оценки блоков</h3>
      <div className={mod.checks}>
        <label>
          <input type="checkbox" checked={weighted} disabled={busy} data-testid="qq-weighted" onChange={(e) => setWeighted(e.target.checked)} /> С весами (декластеризация у проб, объём у блоков)
        </label>
        <label>
          <input type="checkbox" checked={equal} data-testid="qq-equal" onChange={(e) => setEqual(e.target.checked)} /> Одинаковый масштаб
        </label>
        <label>
          <input type="checkbox" checked={log} data-testid="qq-log" onChange={(e) => setLog(e.target.checked)} /> Логарифмические оси
        </label>
      </div>
      {s.qqError && (
        <p className={css.error} role="alert" data-testid="qq-error" data-code={s.qqError.code}>
          {s.qqError.message} {s.qqError.hint}
        </p>
      )}
      {s.qqPhase === "loading" && <p className={css.note}>Q-Q считается…</p>}
      {q && (
        <div className={mod.qq}>
          <figure data-testid="qq-chart" data-points={pts.length}>
            {pts.length ? <QQChart points={pts} marks={marks} log={log} equal={equal} diagonal labels color={BRAND_BLUE} xLabel={weighted ? "Композиты домена (с весами)" : "Композиты домена"} yLabel="Оценка блоков" /> : <p className={css.note}>Нет точек, которые можно показать{log ? " на логарифмических осях" : ""}.</p>}
          </figure>
          <div className={mod.qqSide}>
            <div className={mod.tableWrap}>
              <table className={mod.table} data-testid="qq-table">
                <thead>
                  <tr>
                    <th>{q.field}</th>
                    <th>Число</th>
                    <th>Среднее</th>
                    <th>Дисперсия</th>
                    <th>CV</th>
                  </tr>
                </thead>
                <tbody>
                  {side("X: композиты", q.x)}
                  {side("Y: оценка блоков", q.y)}
                </tbody>
              </table>
            </div>
            <p className={css.note} data-testid="qq-ks">
              {q.ks === null ? "Расстояние Колмогорова–Смирнова не считается." : `Расстояние Колмогорова–Смирнова D = ${num(q.ks, 3)}.`} Точки на диагонали — распределения совпадают; кригинг сглаживает, поэтому хвосты оценки короче хвостов проб.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

export function GlobalWindow({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(globalStore, (x) => x);
  const rows = useSel(estimationStore, (x) => x.rows);
  const loaded = useSel(estimationStore, (x) => x.loaded);
  const jobs = useSel(jobsStore, (x) => x.jobs);
  const job = s.job ? jobs.find((j) => j.source === s.job?.source && j.id === s.job?.id) : undefined;
  const busy = s.phase === "running";
  const done = checkedRows(rows);
  const reason = globalReason(s);
  const swathReason = s.rowId === null ? "Выберите строку «Параметры оценки»" : app.registry.reason("estimate.swath");

  return (
    <div className={mod.window} data-testid="estimation-global">
      <div className={mod.bar}>
        <label>
          <span>Строка «Параметры оценки» (выполненная)</span>
          <select className={mod.select} value={s.rowId ?? ""} disabled={busy} aria-label="Строка оценки" data-testid="global-row" onChange={(e) => setGlobalRow(e.target.value ? Number(e.target.value) : null)}>
            <option value="">— выберите —</option>
            {done.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className={mod.main}>
        {loaded && done.length === 0 && (
          <p className={css.note} data-testid="global-no-rows">
            Нет выполненных строк «Параметры оценки»: проверять нечего. Выполните оценку в окне «План оценки».
          </p>
        )}
        {s.error && (
          <p className={css.error} role="alert" data-testid="global-error" data-code={s.error.code}>
            {s.error.message} {s.error.hint}
          </p>
        )}
        {s.note && (
          <p className={css.note} data-testid="global-note">
            {s.note}
          </p>
        )}
        {s.result && <Summary g={s.result} />}
        {(s.result || s.qq || s.qqError || s.qqPhase === "loading") && <QqPart q={s.qq} busy={s.qqPhase === "loading"} weighted={s.weighted} />}
        {!s.result && !busy && !s.error && !s.note && (
          <p className={mod.empty} data-testid="global-hint">
            Выберите строку и нажмите «Проверить»: среднее оценки сравнивается со средним проб (норма — не больше 5 %), строится Q-Q.
          </p>
        )}
      </div>
      <div className={mod.footer}>
        {busy && (
          <div className={mod.progress} data-testid="global-progress" role="status">
            <progress max={1} value={job?.progress ?? 0} aria-label="Ход проверки" />
            <span className={mod.stage}>{job?.message ?? (job ? "Выполняется" : "Запуск")}</span>
          </div>
        )}
        <div className={mod.actions}>
          <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Сравнить средние и построить Q-Q; в проект ничего не записывается"} data-testid="global-run" onClick={() => void runGlobal().catch(() => undefined)}>
            Проверить
          </button>
          {busy && s.job && (
            <button type="button" className={css.btn} data-testid="global-cancel" title="Отменить: ничего не записано" onClick={() => void cancelGlobal().catch(() => undefined)}>
              Отменить
            </button>
          )}
          <button type="button" className={css.btn} disabled={swathReason !== null} title={swathReason ?? "Окно swath plot: оценка блоков и композиты по полосам"} data-testid="global-swath" onClick={() => s.rowId !== null && void app.run("estimate.swath", { estimation_id: s.rowId }).catch(() => undefined)}>
            Swath…
          </button>
          <span style={{ flex: 1 }} />
          <button type="button" className={css.btn} data-testid="global-close" onClick={() => void requestClose(winId)}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
