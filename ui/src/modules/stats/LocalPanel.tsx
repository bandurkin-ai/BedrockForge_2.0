/**
 * «Карта неопределённости» of the tab (ТЗ 7.29; ADR 0022 §11; перенос BF `local-uncertainty`): the spread of the
 * IDW estimate in plan — a realization is the holes drawn with replacement; the maps of the mean, σ, P10, P90 and
 * of the probability to exceed the threshold on the grid (x — east, y — north: drawn north up, one scale on both
 * axes). A node without data in the radius is null: hatched, «пусто», never 0. The seed is told: «Повторить с
 * этим зерном» gives the same realizations.
 */
import { countsText, fmt, type TabContext } from "./forms";
import { LOCAL_MAPS, localBody, localCsv, localFacts, localGrid, localTitle, type LocalForm, type LocalShow } from "./formsVario";
import { Facts, Field, NumberInput, ProblemBox, RunButton, useWhole } from "./Parts";
import { runAnalysis, setForm, statsStore } from "./state";
import css from "./Stats.module.css";
import { GridPlot } from "./VarioPlots";

export function LocalPanel({ ctx }: { ctx: TabContext }) {
  const s = useWhole(statsStore);
  const form = s.forms.local;
  const run = s.runs.local;
  const r = run.result;
  const reason = localBody(ctx, form).reason ?? null;
  const set = (p: Partial<LocalForm>) => setForm("local", p);
  const show: LocalShow = r && form.show === "prob" && r.prob === null ? "mean" : form.show;
  const grid = r ? localGrid(r, show) : null;
  const title = r && show === "prob" && r.threshold !== null ? `Вероятность ≥ ${fmt(r.threshold)}` : localTitle(show);
  return (
    <div className={css.panel} data-testid="stats-local">
      <div className={css.form}>
        <Field label="Сетка X × Y" hint="Узлов сетки по X и по Y (2–100)">
          <span className={css.pair}>
            <NumberInput label="Узлов по X" value={form.nx} onChange={(nx) => set({ nx })} width={48} />
            <NumberInput label="Узлов по Y" value={form.ny} onChange={(ny) => set({ ny })} width={48} />
          </span>
        </Field>
        <Field label="Соседей" hint="Сколько ближайших проб берёт IDW (1–64)">
          <NumberInput label="Соседей" value={form.k} onChange={(k) => set({ k })} width={48} />
        </Field>
        <Field label="Степень" hint="Степень расстояния IDW (больше 0, до 10)">
          <NumberInput label="Степень" value={form.power} onChange={(power) => set({ power })} width={48} />
        </Field>
        <Field label="Радиус, м" hint="Пробы дальше радиуса не берутся; пусто — 25 % габарита плана">
          <NumberInput label="Радиус" value={form.radius} onChange={(radius) => set({ radius })} placeholder="авто" />
        </Field>
        <Field label="Реализаций" hint="Выборок скважин с возвратом (5–300)">
          <NumberInput label="Реализаций" value={form.realizations} onChange={(realizations) => set({ realizations })} width={52} />
        </Field>
        <Field label="Зерно" hint="Пусто — новое при каждом расчёте; то же зерно даёт те же реализации">
          <NumberInput label="Зерно" value={form.seed} onChange={(seed) => set({ seed })} placeholder="случайное" width={96} />
        </Field>
        <Field label="Порог" hint="Карта вероятности превысить порог; пусто — без неё">
          <NumberInput label="Порог" value={form.threshold} onChange={(threshold) => set({ threshold })} placeholder="нет" />
        </Field>
        <RunButton busy={run.busy} reason={reason} onClick={() => void runAnalysis("local").catch(() => undefined)} />
      </div>
      {reason && <p className={css.hint}>{reason}</p>}
      {run.error && <ProblemBox problem={run.error} />}
      {r && (
        <div className={`${css.results} ${run.busy ? css.stale : ""}`}>
          <div className={css.column}>
            <Field label="Карта">
              <select aria-label="Карта неопределённости" value={show} onChange={(e) => set({ show: e.target.value as LocalShow })}>
                {LOCAL_MAPS.map(([id, l]) => (
                  <option key={id} value={id} disabled={id === "prob" && r.prob === null}>
                    {l}
                    {id === "prob" && r.prob === null ? " (задайте порог)" : ""}
                  </option>
                ))}
              </select>
            </Field>
            <GridPlot
              label={`${title} ${r.table} ${r.field}`}
              xs={r.grid.x}
              ys={r.grid.y}
              values={grid ?? []}
              xTitle="X (восток), м"
              yTitle="Y (север), м"
              valueTitle={title}
              tip={(i, j) => {
                const v = grid?.[j]?.[i] ?? null;
                return `X ${fmt(r.grid.x[i], 8)}, Y ${fmt(r.grid.y[j], 8)}: ${v === null ? "пусто — нет проб в радиусе" : `${title} ${fmt(v, 4)}`}`;
              }}
              fileName={`Неопределённость ${r.table} ${r.field} ${title}`}
              csv={() => localCsv(r)}
            />
          </div>
          <div className={css.column}>
            <Facts rows={localFacts(r)} label="Итоги карты неопределённости" />
            <p className={css.note}>
              Зерно {r.seed}.{" "}
              <button type="button" className={css.link} onClick={() => set({ seed: String(r.seed) })}>
                Повторить с этим зерном
              </button>
            </p>
            <p className={css.note}>{countsText(r.counts)}</p>
          </div>
        </div>
      )}
    </div>
  );
}
