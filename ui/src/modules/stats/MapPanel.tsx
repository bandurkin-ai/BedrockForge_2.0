/**
 * «Карта вариограммы» of the tab (ТЗ 7.29; ADR 0022 §11; перенос BF `variogram-map`): γ by the shifts (ΔX, ΔY) in
 * plan — a column i is ΔX = u[i] (east), a row j is ΔY = v[j] (north, from the south): drawn north up, east to the
 * right, one scale on both axes (unlike the window «Вариография», where a1 is up — 16.10). A cell with fewer pairs
 * than the threshold is null: hatched, «пусто», never 0. «Открыть в окне «Вариография»» — the map in any plane.
 */
import { countsText, fmt, type TabContext } from "./forms";
import { mapBody, mapCsv, mapFacts, type MapForm } from "./formsVario";
import { Facts, Field, NumberInput, OpenVarioButton, ProblemBox, RunButton, useWhole } from "./Parts";
import { runAnalysis, setForm, statsStore } from "./state";
import css from "./Stats.module.css";
import { GridPlot } from "./VarioPlots";

export function MapPanel({ ctx }: { ctx: TabContext }) {
  const s = useWhole(statsStore);
  const form = s.forms.map;
  const run = s.runs.map;
  const r = run.result;
  const reason = mapBody(ctx, form).reason ?? null;
  const set = (p: Partial<MapForm>) => setForm("map", p);
  return (
    <div className={css.panel} data-testid="stats-map">
      <div className={css.form}>
        <Field label="Ячейка, м" hint="Размер ячейки сдвигов; пусто — круглый шаг 1/20 габарита плана">
          <NumberInput label="Ячейка" value={form.cell} onChange={(cell) => set({ cell })} placeholder="авто" />
        </Field>
        <Field label="Ячеек в каждую сторону" hint="Сдвиги от −n до n ячеек по ΔX и ΔY (1–100)">
          <NumberInput label="Ячеек в каждую сторону" value={form.n} onChange={(n) => set({ n })} width={52} />
        </Field>
        <Field label="Пар не меньше" hint="Ячейка с меньшим числом пар пуста (1–1000)">
          <NumberInput label="Пар не меньше" value={form.minPairs} onChange={(minPairs) => set({ minPairs })} width={52} />
        </Field>
        <label className={css.check} title="γ делится на дисперсию σ² значений">
          <input type="checkbox" checked={form.standardize} onChange={(e) => set({ standardize: e.target.checked })} />γ / σ²
        </label>
        <RunButton busy={run.busy} reason={reason} onClick={() => void runAnalysis("map").catch(() => undefined)} />
        <OpenVarioButton ctx={ctx} view="map" />
      </div>
      {reason && <p className={css.hint}>{reason}</p>}
      {run.error && <ProblemBox problem={run.error} vario={ctx} />}
      {r && (
        <div className={`${css.results} ${run.busy ? css.stale : ""}`}>
          <GridPlot
            label={`Карта вариограммы ${r.table} ${r.field}`}
            xs={r.u}
            ys={r.v}
            values={r.gamma}
            xTitle="ΔX (восток), м"
            yTitle="ΔY (север), м"
            valueTitle={r.standardize ? "γ / σ²" : "γ"}
            centre
            tip={(i, j) => {
              const g = r.gamma[j]?.[i] ?? null;
              const n = r.n[j]?.[i] ?? 0;
              return `ΔX ${fmt(r.u[i])} м, ΔY ${fmt(r.v[j])} м: ${g === null ? `пусто (пар ${n.toLocaleString("ru-RU")} < ${r.min_pairs})` : `γ ${fmt(g, 4)}, пар ${n.toLocaleString("ru-RU")}`}`;
            }}
            fileName={`Карта вариограммы ${r.table} ${r.field}`}
            csv={() => mapCsv(r)}
          />
          <div className={css.column}>
            <Facts rows={mapFacts(r)} label="Итоги карты вариограммы" />
            <p className={css.note}>Север вверху, восток справа; пустая ячейка — пар меньше порога.</p>
            <p className={css.note}>{countsText(r.counts)}</p>
          </div>
        </div>
      )}
    </div>
  );
}
