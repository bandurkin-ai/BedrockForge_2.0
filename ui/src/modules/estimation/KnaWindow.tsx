/**
 * The window «Анализ окрестности» (KNA; ТЗ 8.8.5; ADR 0024 решение 14, № 80; docs/progress/e18-api.md §8.2): the kriging row, the parameters
 * to vary (n_max, the radius, the discretization, the size of the block), the test blocks (three automatically or the person's own),
 * the job with its progress and «Отменить», the charts SLOPE / KE / KV / NS by the values of the shown parameter — a line per block —,
 * the table of the variants of a block and the recommendation of `n_max` with its explanation. The recommendation is never applied
 * by itself: «Применить к параметрам…» opens the row in the wizard with the value put in (`applyNMax`), the person saves it there.
 * Floating, not modal; the analysis writes nothing, so it runs on a project open for reading too.
 */
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { requestClose } from "../../shell/windows";
import { jobsStore } from "../jobs/state";
import { KNA_AXIS, KNA_PARAMS, KNA_TITLES } from "./checksApi";
import mod from "./Checks.module.css";
import { useSel } from "./hooks";
import { blockColor, INDICATORS, KnaChart } from "./KnaChart";
import { BlockPicker, VariantTable } from "./KnaParts";
import { applyNMax, blockShort, blockTitle, cancelKna, knaReason, knaRows, knaStore, recommendText, runKna, setBlockIndex, setKnaRow, setShown, toggleVary } from "./knaState";
import { estimationStore } from "./state";

export function KnaWindow({ winId }: { winId: string }) {
  const app = useApp();
  const s = useSel(knaStore, (x) => x);
  const rows = useSel(estimationStore, (x) => x.rows);
  const loaded = useSel(estimationStore, (x) => x.loaded);
  const jobs = useSel(jobsStore, (x) => x.jobs);
  const job = s.job ? jobs.find((j) => j.source === s.job?.source && j.id === s.job?.id) : undefined;
  const busy = s.phase === "running";
  const kriging = knaRows(rows);
  const reason = knaReason(s);
  const r = s.result;
  const shown = r && r.vary.includes(s.shown) ? s.shown : (r?.vary[0] ?? s.shown);
  const block = r ? (r.blocks[s.blockIndex] ?? r.blocks[0]) : undefined;
  const applyReason = r?.recommend === null || !r ? "Рекомендации нет" : app.registry.reason("estimate.open");
  const start = () => void runKna().catch(() => undefined); // a refusal stands under the bar (`error`)

  return (
    <div className={mod.window} data-testid="estimation-kna">
      <div className={mod.bar}>
        <label>
          <span>Строка «Параметры оценки» (SK, OK)</span>
          <select className={mod.select} value={s.rowId ?? ""} disabled={busy} aria-label="Строка оценки" data-testid="kna-row" onChange={(e) => setKnaRow(e.target.value ? Number(e.target.value) : null)}>
            <option value="">— выберите —</option>
            {kriging.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <fieldset className={mod.checks} style={{ border: 0, margin: 0, padding: 0 }} aria-label="Что перебирать">
          <span className={mod.legendTitle}>Перебирать</span>
          {KNA_PARAMS.map((p) => (
            <label key={p} title={`${KNA_TITLES[p]}: по одному параметру, остальные — как в строке`}>
              <input type="checkbox" checked={s.vary.includes(p)} disabled={busy} data-testid={`kna-vary-${p}`} onChange={() => toggleVary(p)} /> {KNA_TITLES[p]}
            </label>
          ))}
        </fieldset>
      </div>
      <div className={mod.main}>
        {loaded && kriging.length === 0 && (
          <p className={css.note} data-testid="kna-no-rows">
            В проекте нет строк с методом SK или OK: анализ окрестности есть только у кригинга. Создайте строку в мастере «Оценка содержаний».
          </p>
        )}
        <BlockPicker disabled={busy} />
        {s.error && (
          <p className={css.error} role="alert" data-testid="kna-error" data-code={s.error.code}>
            {s.error.message} {s.error.hint}
          </p>
        )}
        {s.note && (
          <p className={css.note} data-testid="kna-note">
            {s.note}
          </p>
        )}
        {r && block && (
          <>
            {r.warnings.length > 0 && (
              <ul className={mod.notes} data-testid="kna-warnings">
                {r.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            )}
            <section className={mod.panel} data-testid="kna-recommend" data-n-max={r.recommend ?? ""}>
              <h3>Рекомендация</h3>
              <p>{recommendText(r)}</p>
              {r.blocks.length > 1 && r.recommended && (
                <p className={css.note}>По блокам: {r.blocks.map((b, i) => `${blockShort(b, i)} — ${b.recommend ?? "нет"}`).join("; ")}.</p>
              )}
              <div className={mod.actions}>
                <button type="button" className={css.primary} disabled={applyReason !== null} title={applyReason ?? "Открыть строку в мастере с этим n_max; сохранить её нужно там"} data-testid="kna-apply" onClick={() => r.recommend !== null && void applyNMax(app, r.estimation_id, r.recommend).catch(() => undefined)}>
                  Применить к параметрам…
                </button>
                <span className={css.note}>Само ничего не меняется: откроется правка строки, сохраните её в мастере.</span>
              </div>
            </section>
            <div className={mod.checks} role="group" aria-label="Параметр на графиках">
              <span className={mod.legendTitle}>Графики по значениям:</span>
              <span className={mod.seg}>
                {r.vary.map((p) => (
                  <button key={p} type="button" aria-pressed={p === shown} data-testid={`kna-shown-${p}`} onClick={() => setShown(p)}>
                    {KNA_TITLES[p]}
                  </button>
                ))}
              </span>
            </div>
            <div className={mod.legend} data-testid="kna-legend">
              {r.blocks.map((b, i) => (
                <span key={i} title={blockTitle(b, i)}>
                  <i className={mod.swatch} style={{ background: blockColor(i) }} /> {blockTitle(b, i)}
                </span>
              ))}
            </div>
            <div className={mod.grid} data-testid="kna-charts" data-param={shown}>
              {INDICATORS.map((ind) => (
                <figure key={ind.key} data-testid={`kna-chart-${ind.key}`} title={ind.hint}>
                  <figcaption>
                    {ind.title} · {KNA_AXIS[shown]}
                  </figcaption>
                  <KnaChart param={shown} indicator={ind.key} blocks={r.blocks} recommend={r.recommend} />
                </figure>
              ))}
            </div>
            <div className={mod.checks}>
              <label>
                <span className={mod.legendTitle}>Таблица блока: </span>
                <select className={mod.select} style={{ width: "auto" }} value={r.blocks.indexOf(block)} aria-label="Блок таблицы" data-testid="kna-block-select" onChange={(e) => setBlockIndex(Number(e.target.value))}>
                  {r.blocks.map((b, i) => (
                    <option key={i} value={i}>
                      {blockTitle(b, i)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <VariantTable result={r} block={block} param={shown} />
            {r.candidates !== null && <p className={css.note}>Блоков, до которых дотягиваются первые проходы: {r.candidates.toLocaleString("ru-RU")}; тестовые выбраны по квантилям числа проб 0,9 / 0,5 / 0,1.</p>}
          </>
        )}
        {!r && !busy && !s.error && !s.note && (
          <p className={mod.empty} data-testid="kna-hint">
            Выберите строку и нажмите «Выполнить анализ»: три блока и до 16 вариантов каждого параметра считаются за секунды.
          </p>
        )}
      </div>
      <div className={mod.footer}>
        {busy && (
          <div className={mod.progress} data-testid="kna-progress" role="status">
            <progress max={1} value={job?.progress ?? 0} aria-label="Ход анализа" />
            <span className={mod.stage}>{job?.message ?? (job ? "Выполняется" : "Запуск")}</span>
          </div>
        )}
        <div className={mod.actions}>
          <button type="button" className={css.primary} disabled={reason !== null} title={reason ?? "Перебрать параметры на тестовых блоках; в проект ничего не записывается"} data-testid="kna-run" onClick={start}>
            Выполнить анализ
          </button>
          {busy && s.job && (
            <button type="button" className={css.btn} data-testid="kna-cancel" title="Отменить: ничего не записано" onClick={() => void cancelKna().catch(() => undefined)}>
              Отменить
            </button>
          )}
          <span style={{ flex: 1 }} />
          <button type="button" className={css.btn} data-testid="kna-close" onClick={() => void requestClose(winId)}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
