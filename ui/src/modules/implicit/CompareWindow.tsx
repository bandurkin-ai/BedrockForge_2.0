/**
 * The window «Сверка данных и модели» (ТЗ 7.35 Д-33; docs/progress/e19-api.md §8.3): the model, the set, the table and the field of the codes;
 * then the matrix «код данных × код модели» (the length in metres or the number of intervals; the diagonal — what agrees, the tint — how much
 * of the whole a cell holds), the share of the agreement and the list of the intervals that do not agree, the longest first, with a way to
 * the window «Данные по скважинам» (7.17) on the hole. The command is `implicit.compare_holes`. The window is floating, not modal.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { errorText, num } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { requestClose } from "../../shell/windows";
import { loadFields, loadSets, loadTables, resolveTable, type FieldRow, type SetRow, type TableRow } from "../composites/api";
import { MISMATCH_SIZES } from "./compareApi";
import { cellValue, colSum, facts, isDiagonal, METRICS, METRIC_TITLES, mismatchRange, pagesOf, remarks, rowSum, valueText, weight, type Metric } from "./compareModel";
import { choose, compareStore, goPage, gotoHole, ready, resetCompare, runCompare } from "./CompareState";
import { whenGone } from "./CheckState";
import mod from "./Check.module.css";
import { useLatest, useSel } from "./hooks";
import { implicitStore } from "./state";

export function CompareWindow({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(compareStore, (x) => x);
  const rows = useSel(implicitStore, (x) => x.rows);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => whenGone(winId, resetCompare), [winId]);
  const fail = (e: unknown) => setProblem(errorText(e));
  const sets = useLatest<SetRow[]>(loadSets, [], fail);
  const tables = useLatest<TableRow[]>(s.setId === null ? null : () => loadTables(s.setId!), [s.setId], fail);
  const tableKey = s.table && tables ? (resolveTable(tables, s.table) ?? s.table) : null;
  const tableKnown = !!tableKey && !!tables?.some((t) => t.table === tableKey);
  const fields = useLatest<FieldRow[]>(s.setId === null || !tableKnown ? null : () => loadFields(s.setId!, tableKey!), [s.setId, tableKey, tableKnown], fail);
  const fieldKnown = !!s.field && !!fields?.some((x) => x.name === s.field);
  const built = rows.filter((r) => r.stats !== null || r.outputs > 0);
  const a = s.answer;
  const busy = s.phase === "loading";
  const runReason = s.modelId === null ? "Выберите модель" : !ready(s) ? "Выберите набор скважин, таблицу и поле кода" : busy ? "Идёт сверка" : null;
  const run = () => void app.run("implicit.compare_holes", { model_id: s.modelId, set_id: s.setId, table: s.table, field: s.field }).catch(() => undefined); // a refusal stands under the bar
  const pages = a ? pagesOf(a.mismatches.total, a.mismatches.size) : 1;
  return (
    <div className={mod.window} data-testid="implicit-compare">
      <div className={mod.bar}>
        <label>
          <span>Неявная модель</span>
          <select className={mod.select} value={s.modelId ?? ""} aria-label="Неявная модель" data-testid="cmp-model" onChange={(e) => compareStore.set({ modelId: e.target.value ? Number(e.target.value) : null, answer: null, phase: "idle" })}>
            <option value="">— выберите —</option>
            {built.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Набор скважин</span>
          <select className={mod.select} value={s.setId ?? ""} aria-label="Набор скважин" data-testid="cmp-set" onChange={(e) => choose({ setId: e.target.value ? Number(e.target.value) : null })}>
            <option value="">{sets ? "— выберите —" : "Чтение…"}</option>
            {(sets ?? []).map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Таблица кодов</span>
          <select className={mod.select} value={tableKnown ? tableKey! : ""} disabled={s.setId === null} aria-label="Таблица кодов" data-testid="cmp-table" onChange={(e) => choose({ table: e.target.value || null })}>
            <option value="">{s.setId === null ? "—" : tables ? "— выберите —" : "Чтение…"}</option>
            {(tables ?? []).map((t) => (
              <option key={t.table} value={t.table}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Поле кода</span>
          <select className={mod.select} value={fieldKnown ? s.field! : ""} disabled={!tableKnown} aria-label="Поле кода" data-testid="cmp-field" onChange={(e) => choose({ field: e.target.value || null })}>
            <option value="">{tableKnown ? (fields ? "— выберите —" : "Чтение…") : "—"}</option>
            {(fields ?? [])
              .filter((x) => x.kind === "text")
              .map((x) => (
                <option key={x.key} value={x.name}>
                  {x.name}
                </option>
              ))}
          </select>
        </label>
        <div className={mod.actions}>
          <button type="button" className={css.primary} disabled={runReason !== null} title={runReason ?? "Сравнить код каждого интервала таблицы с кодом модели по наибольшей длине пересечения ствола с телами"} data-testid="cmp-run" onClick={run}>
            Сверить
          </button>
        </div>
      </div>
      <div className={mod.main}>
        {problem && (
          <p className={css.error} role="alert" data-testid="cmp-problem">
            {problem}
          </p>
        )}
        {s.error && (
          <p className={css.error} role="alert" data-testid="cmp-error">
            {s.error}
          </p>
        )}
        {s.note && (
          <p className={css.note} role="status" data-testid="cmp-note">
            {s.note}
          </p>
        )}
        {s.phase === "idle" && !a && <p className={mod.empty}>Выберите модель и таблицу кодов набора и нажмите «Сверить»: матрица покажет, какие коды данных модель отнесла к каким кодам.</p>}
        {busy && !a && (
          <p className={mod.empty} role="status" data-testid="cmp-loading">
            Сверяю интервалы с телами модели…
          </p>
        )}
        {a && (
          <>
            <p className={mod.info} data-testid="cmp-info">
              Модель «{a.model.name}» · таблица «{a.table.name}», поле {a.table.field}
            </p>
            {a.model.stale_reason && (
              <p className={mod.banner} role="status" data-testid="cmp-stale">
                Модель устарела: {a.model.stale_reason}. «Обновить» пересчитает изменённые участки.
              </p>
            )}
            <dl className={mod.facts} data-testid="cmp-facts">
              {facts(a).map((f) => (
                <div key={f.key} className={mod.fact} title={f.hint} data-testid={`cmp-fact-${f.key}`}>
                  <dt>{f.title}</dt>
                  <dd>{f.value}</dd>
                </div>
              ))}
            </dl>
            {remarks(a).map((t) => (
              <p key={t} className={css.note} data-testid="cmp-remark">
                {t}
              </p>
            ))}
            <div className={mod.tabs} role="tablist" aria-label="Что считать в матрице">
              {METRICS.map((m: Metric) => (
                <button key={m} type="button" role="tab" aria-selected={s.metric === m} data-testid={`cmp-metric-${m}`} onClick={() => choose({ metric: m })}>
                  {METRIC_TITLES[m]}
                </button>
              ))}
            </div>
            <div className={mod.matrixWrap}>
              <table className={mod.matrix} data-testid="cmp-matrix" data-metric={s.metric}>
                <caption className={mod.caption}>Строки — код в данных, столбцы — код в модели; рамка — совпадение.</caption>
                <thead>
                  <tr>
                    <th scope="col" className={mod.rowHead}>
                      Данные ↓ · Модель →
                    </th>
                    {a.model_codes.map((c) => (
                      <th key={c} scope="col">
                        {c}
                      </th>
                    ))}
                    <th scope="col" className={mod.sum}>
                      Всего
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {a.data_codes.map((code, r) => (
                    <tr key={code}>
                      <th scope="row" className={mod.rowHead}>
                        {code}
                      </th>
                      {a.model_codes.map((mc, c) => {
                        const v = cellValue(a, s.metric, r, c);
                        const w = weight(a, s.metric, r, c);
                        const diag = isDiagonal(a, r, c);
                        const off = !diag && v !== null && v > 0;
                        return (
                          <td
                            key={mc}
                            data-agree={diag}
                            data-off={off}
                            data-zero={v === 0}
                            data-testid={`cmp-cell-${r}-${c}`}
                            title={`${code} → ${mc}: ${valueText(v, s.metric)}`}
                            style={w > 0 ? { background: `color-mix(in srgb, ${diag ? "var(--accent)" : "var(--warn)"} ${Math.round(8 + w * 70)}%, transparent)` } : undefined}
                          >
                            {valueText(v, s.metric)}
                          </td>
                        );
                      })}
                      <td className={mod.sum} data-testid={`cmp-rowsum-${r}`}>
                        {valueText(rowSum(a, s.metric, r), s.metric)}
                      </td>
                    </tr>
                  ))}
                  <tr>
                    <th scope="row" className={`${mod.rowHead} ${mod.sum}`}>
                      Всего
                    </th>
                    {a.model_codes.map((mc, c) => (
                      <td key={mc} className={mod.sum} data-testid={`cmp-colsum-${c}`}>
                        {valueText(colSum(a, s.metric, c), s.metric)}
                      </td>
                    ))}
                    <td className={mod.sum} />
                  </tr>
                </tbody>
              </table>
            </div>
            <p className={mod.caption} data-testid="cmp-list-title">
              Интервалы, где код модели не совпал с кодом данных: {num(a.mismatches.total, 0)}; сначала самые длинные.
            </p>
            <div className={mod.tableWrap}>
              <table className={mod.table} data-testid="cmp-list" data-rows={a.mismatches.rows.length}>
                <thead>
                  <tr>
                    <th scope="col">Скважина</th>
                    <th scope="col" className={mod.num}>
                      От–до, м
                    </th>
                    <th scope="col">Код данных</th>
                    <th scope="col">Код модели</th>
                    <th scope="col" className={mod.num}>
                      Длина, м
                    </th>
                    <th scope="col" />
                  </tr>
                </thead>
                <tbody>
                  {a.mismatches.rows.map((m, k) => (
                    <tr key={`${m.hole_id}-${m.from}`} data-picked={s.picked === k} data-testid={`cmp-row-${k}`} onClick={() => compareStore.set({ picked: k })}>
                      <td>{m.hole}</td>
                      <td className={mod.num}>{mismatchRange(m)}</td>
                      <td>{m.data_code}</td>
                      <td>{m.model_code}</td>
                      <td className={mod.num}>{m.length === null ? "—" : num(m.length, 2)}</td>
                      <td>
                        <button type="button" className={css.btn} title="Выбрать скважину и открыть окно «Данные по скважинам» (7.17)" data-testid={`cmp-goto-${k}`} onClick={(e) => (e.stopPropagation(), void gotoHole(app, m))}>
                          К скважине
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!a.mismatches.rows.length && <p className={mod.empty}>Несовпавших интервалов нет.</p>}
            </div>
            <div className={mod.pager}>
              <button type="button" className={css.btn} disabled={busy || a.mismatches.page <= 1} data-testid="cmp-prev" onClick={() => void goPage(a.mismatches.page - 1)}>
                ‹ Назад
              </button>
              <span data-testid="cmp-page">
                Страница {num(a.mismatches.page, 0)} из {num(pages, 0)}
              </span>
              <button type="button" className={css.btn} disabled={busy || a.mismatches.page >= pages} data-testid="cmp-next" onClick={() => void goPage(a.mismatches.page + 1)}>
                Вперёд ›
              </button>
              <span className={mod.grow} />
              <label>
                Строк на странице{" "}
                <select
                  value={s.size}
                  aria-label="Строк на странице"
                  data-testid="cmp-size"
                  onChange={(e) => {
                    choose({ size: Number(e.target.value) });
                    void runCompare(1);
                  }}
                >
                  {MISMATCH_SIZES.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </>
        )}
      </div>
      <div className={mod.footer}>
        <div className={css.row}>
          <span style={{ flex: 1 }} />
          <button type="button" className={css.btn} data-testid="cmp-close" onClick={() => void requestClose(winId)}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
