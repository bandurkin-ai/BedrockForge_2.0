/**
 * The tab «План / факт» of the window «Проектная скважина» (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решение 29): after drilling, the actual hole of a set against
 * the planned one at every depth, 1 m apart — the largest deviation and its depth, the lengths (planned, actual, compared), the chart of the
 * deviation by depth (`PlannedChart.tsx`) and the table (ΔX, ΔY, ΔZ — actual minus planned, d — the distance between the points). Equal paths give 0.
 * The core reads the STORED planned hole, so the window asks to save first. The command is `planned.compare`.
 */
import { useState } from "react";
import { loadHoleNames } from "../../charts/striplog/data";
import { useApp } from "../../shell/AppContext";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { loadSets, type SetRow } from "../composites/api";
import { useLatest, useSel } from "../estimation/hooks";
import mod from "./Planned.module.css";
import { PlannedChart } from "./PlannedChart";
import { lengthText } from "./plannedModel";
import { askReason, checkSetOf, plannedCheck, plannedWin, setCheckSet, setHole } from "./plannedState";

const PAGE = 50;
const fixed = (v: number): string => v.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 3 });

export function PlannedCompare() {
  const app = useApp();
  const w = useSel(plannedWin, (x) => x);
  const hole = useSel(plannedCheck, (x) => x.hole);
  const setId = useSel(plannedCheck, (x) => checkSetOf(x, w.form));
  const run = useSel(plannedCheck, (x) => x.compare);
  const [problem, setProblem] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const fail = (e: unknown) => setProblem(errorText(e));
  const sets = useLatest<SetRow[]>(loadSets, [], fail);
  const names = useLatest<string[]>(setId === null ? null : () => loadHoleNames(setId), [setId], fail);
  const a = run.answer;
  const reason = askReason() ?? (setId === null ? "Выберите набор скважин" : !hole ? "Выберите фактическую скважину" : run.phase === "loading" ? "Идёт сравнение" : null);
  const go = () => {
    setPage(0);
    void app.run("planned.compare", { id: w.id, set_id: setId, hole }).catch(() => undefined); // a refusal stands under the button
  };
  const pages = a ? Math.max(1, Math.ceil(a.rows.length / PAGE)) : 1;
  const shown = a ? a.rows.slice(page * PAGE, page * PAGE + PAGE) : [];
  return (
    <div data-testid="pl-compare">
      <fieldset className={css.group}>
        <legend>Фактическая скважина</legend>
        <label className={css.formRow}>
          <span>Набор скважин</span>
          <select className={css.input} value={setId ?? ""} aria-label="Набор скважин для сравнения" data-testid="pl-cmp-set" onChange={(e) => setCheckSet(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{sets ? "— выберите —" : "Чтение…"}</option>
            {(sets ?? []).map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <label className={css.formRow}>
          <span>Скважина набора</span>
          <select className={css.input} value={hole ?? ""} disabled={setId === null} aria-label="Скважина набора" data-testid="pl-cmp-hole" onChange={(e) => setHole(e.target.value || null)}>
            <option value="">{setId === null ? "—" : names ? "— выберите —" : "Чтение…"}</option>
            {(names ?? []).map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <div className={css.row}>
          <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Отклонение фактической траектории от проектной по глубинам, через 1 м"} data-testid="pl-compare-run" onClick={go}>
            Сравнить
          </button>
          {run.phase === "loading" && (
            <span className={css.note} role="status">
              Считаю…
            </span>
          )}
          {reason && run.phase !== "loading" && (
            <span className={css.note} data-testid="pl-compare-reason">
              {reason}
            </span>
          )}
        </div>
        {problem && (
          <p className={css.error} role="alert" data-testid="pl-compare-problem">
            {problem}
          </p>
        )}
        {run.error && (
          <p className={css.error} role="alert" data-testid="pl-compare-error">
            {run.error}
          </p>
        )}
      </fieldset>
      {a && (
        <div className={mod.page} data-testid="pl-compare-answer" style={{ padding: 0, marginTop: 10 }}>
          <dl className={mod.facts}>
            <div className={mod.fact}>
              <dt>Наибольшее отклонение</dt>
              <dd data-testid="pl-max">{lengthText(a.max)}</dd>
            </div>
            <div className={mod.fact}>
              <dt>На глубине</dt>
              <dd>{lengthText(a.max_depth)}</dd>
            </div>
            <div className={mod.fact} title="Сравнивается меньшая из двух длин">
              <dt>Сравнено</dt>
              <dd>{lengthText(a.length.compared)}</dd>
            </div>
            <div className={mod.fact}>
              <dt>Длина плана / факта</dt>
              <dd>
                {lengthText(a.length.planned)} / {lengthText(a.length.actual)}
              </dd>
            </div>
          </dl>
          <div className={mod.chart} data-testid="pl-chart-box">
            <PlannedChart answer={a} />
          </div>
          <div className={css.tableWrap}>
            <table className={css.table} data-testid="pl-compare-table" data-rows={a.rows.length}>
              <thead>
                <tr>
                  <th>Глубина, м</th>
                  <th>ΔX, м</th>
                  <th>ΔY, м</th>
                  <th>ΔZ, м</th>
                  <th>d, м</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.depth}>
                    <td className={mod.num}>{fixed(r.depth)}</td>
                    <td className={mod.num}>{fixed(r.dx)}</td>
                    <td className={mod.num}>{fixed(r.dy)}</td>
                    <td className={mod.num}>{fixed(r.dz)}</td>
                    <td className={mod.num}>{fixed(r.d)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={css.pager}>
            <button type="button" className={css.btn} disabled={page <= 0} onClick={() => setPage(page - 1)} data-testid="pl-cmp-prev">
              ‹
            </button>
            <span>
              Строки {a.rows.length ? page * PAGE + 1 : 0}–{Math.min(a.rows.length, page * PAGE + PAGE)} из {a.rows.length}
            </span>
            <button type="button" className={css.btn} disabled={page + 1 >= pages} onClick={() => setPage(page + 1)} data-testid="pl-cmp-next">
              ›
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
