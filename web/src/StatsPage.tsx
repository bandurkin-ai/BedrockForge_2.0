import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  runDeclustering, runNscore, runVariogram, runVariogramMap,
  type DeclusParams, type DeclusResult, type DirectionParams, type NscoreResult, type Summary,
  runBootstrap, runLocalUncertainty, getNumericFields, type NumericField,
  type BootstrapResult, type Grid, type LocalResult,
  type VariogramMapResult, type VariogramModelParams, type VariogramResult, type Hole,
} from "./api";
import { formatNumber, parseNumber } from "./stats/chartMath";
import { effectiveTable, useSampleTables } from "./shell/SampleTableSelect";
import { Heatmap, Histogram, LineChart, type Series } from "./stats/charts";
import { coordFormat, defaultLag, planSpan } from "./stats/defaults";
import { ANALYSES, type StatsView } from "./stats/analyses";
import { ColorScale } from "./viz/colorScale";

interface Run<T> {
  busy: boolean;
  error: string | null;
  result: T | null;
  start: (job: () => Promise<T>) => void;
}

/** One calculation at a time: a newer start discards the answer of an older one. */
function useRun<T>(): Run<T> {
  const [state, setState] = useState<{ busy: boolean; error: string | null; result: T | null }>({ busy: false, error: null, result: null });
  const ticket = useRef(0);
  useEffect(() => () => void (ticket.current = -1), []);
  const start = (job: () => Promise<T>) => {
    const mine = ++ticket.current;
    setState((s) => ({ ...s, busy: true, error: null }));
    job().then(
      (result) => mine === ticket.current && setState({ busy: false, error: null, result }),
      (e: Error) => mine === ticket.current && setState((s) => ({ ...s, busy: false, error: e.message })),
    );
  };
  return { ...state, start };
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="stats-field" title={hint}>
      <span>{label}</span>
      {children}
    </label>
  );
}

function NumberInput({ value, onChange, placeholder, width = 70 }: { value: string; onChange: (v: string) => void; placeholder?: string; width?: number }) {
  return <input type="text" inputMode="decimal" value={value} placeholder={placeholder} style={{ width }} onChange={(e) => onChange(e.target.value)} />;
}

function RunButton({ busy, disabled, onClick, children = "Рассчитать" }: { busy: boolean; disabled?: boolean; onClick: () => void; children?: ReactNode }) {
  return (
    <button className="primary" onClick={onClick} disabled={busy || disabled}>
      {busy ? "Расчёт…" : children}
    </button>
  );
}

function Results({ run, children }: { run: Pick<Run<unknown>, "busy" | "error" | "result">; children: ReactNode }) {
  return (
    <>
      {run.error && <div className="alert error">{run.error}</div>}
      {run.result !== null && <div className={run.busy ? "stats-results stale" : "stats-results"}>{children}</div>}
    </>
  );
}

function Summary2({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="stats-summary">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

// ---- declustering parameters (shared by declustering and normal scores) ----

interface DeclusForm {
  method: "cell" | "polygonal";
  cmin: string;
  cmax: string;
  ncell: string;
  noff: string;
  minmax: "min" | "max";
  nx: string;
}

const DEFAULT_DECLUS: DeclusForm = { method: "cell", cmin: "", cmax: "", ncell: "20", noff: "8", minmax: "min", nx: "100" };

function declusParams(f: DeclusForm): DeclusParams {
  const grid = Math.round(parseNumber(f.nx) ?? 100);
  return {
    method: f.method,
    cmin: parseNumber(f.cmin),
    cmax: parseNumber(f.cmax),
    ncell: Math.round(parseNumber(f.ncell) ?? 20),
    noff: Math.round(parseNumber(f.noff) ?? 8),
    minmax: f.minmax,
    nx: grid,
    ny: grid,
  };
}

function DeclusFields({ form, onChange }: { form: DeclusForm; onChange: (next: DeclusForm) => void }) {
  const set = (patch: Partial<DeclusForm>) => onChange({ ...form, ...patch });
  return (
    <>
      <Field label="Метод">
        <select value={form.method} onChange={(e) => set({ method: e.target.value as DeclusForm["method"] })}>
          <option value="cell">Ячейки</option>
          <option value="polygonal">Полигоны (ближайшая проба)</option>
        </select>
      </Field>
      {form.method === "cell" ? (
        <>
          <Field label="Ячейка от" hint="Наименьший размер ячейки; пусто — 2 % протяжённости плана"><NumberInput value={form.cmin} onChange={(cmin) => set({ cmin })} placeholder="авто" /></Field>
          <Field label="до" hint="Наибольший размер ячейки; пусто — 50 % протяжённости плана"><NumberInput value={form.cmax} onChange={(cmax) => set({ cmax })} placeholder="авто" /></Field>
          <Field label="Шагов"><NumberInput value={form.ncell} onChange={(ncell) => set({ ncell })} width={50} /></Field>
          <Field label="Сдвигов" hint="Число сдвигов начала сетки"><NumberInput value={form.noff} onChange={(noff) => set({ noff })} width={50} /></Field>
          <Field label="Критерий" hint="Минимум — для данных, пробуренных гуще в богатых зонах">
            <select value={form.minmax} onChange={(e) => set({ minmax: e.target.value as "min" | "max" })}>
              <option value="min">Минимум среднего</option>
              <option value="max">Максимум среднего</option>
            </select>
          </Field>
        </>
      ) : (
        <Field label="Сетка" hint="Число узлов по каждой оси для оценки площади полигонов"><NumberInput value={form.nx} onChange={(nx) => set({ nx })} width={56} /></Field>
      )}
    </>
  );
}

// ---- panels ----

interface PanelProps {
  setId: number;
  component: string;
  /** the set's sample table; null: the default one */
  table: string | null;
}

function DeclusPanel({ setId, component, table }: PanelProps) {
  const [form, setForm] = useState(DEFAULT_DECLUS);
  const run = useRun<DeclusResult>();
  const r = run.result;
  const change = r ? ((r.declustered_mean - r.naive_mean) / (r.naive_mean || 1)) * 100 : 0;
  const series: Series[] = r?.cell_sizes && r.means ? [{ label: "Среднее", x: r.cell_sizes.slice(1), y: r.means.slice(1) }] : [];
  return (
    <>
      <div className="stats-form">
        <DeclusFields form={form} onChange={setForm} />
        <RunButton busy={run.busy} onClick={() => run.start(() => runDeclustering(setId, component, declusParams(form), table))} />
      </div>
      <Results run={run}>
        {r && (
          <div className="stats-body">
            <Summary2
              rows={[
                ["Интервалов", String(r.count)],
                ["Среднее без весов", formatNumber(r.naive_mean, 4)],
                ["Среднее с весами", formatNumber(r.declustered_mean, 4)],
                ["Изменение", `${change >= 0 ? "+" : ""}${change.toFixed(1)} %`],
                ["Вес: мин / макс", `${formatNumber(r.weight_min)} / ${formatNumber(r.weight_max)}`],
                ...(r.method === "cell" ? ([["Лучший размер ячейки", r.best_cell_size === null ? "улучшения нет, веса равны" : formatNumber(r.best_cell_size)]] as [string, string][]) : []),
              ]}
            />
            {series.length > 0 && (
              <LineChart series={series} xLabel="Размер ячейки" yLabel={`Среднее ${component}`} hLine={{ value: r.naive_mean, label: "без весов" }} />
            )}
          </div>
        )}
      </Results>
    </>
  );
}

function NscorePanel({ setId, component, table }: PanelProps) {
  const [weighted, setWeighted] = useState(false);
  const [form, setForm] = useState(DEFAULT_DECLUS);
  const run = useRun<NscoreResult>();
  const r = run.result;
  const mean = r ? r.sample_scores.reduce((a, b) => a + b, 0) / r.sample_scores.length : 0;
  const std = r ? Math.sqrt(r.sample_scores.reduce((a, b) => a + (b - mean) ** 2, 0) / r.sample_scores.length) : 0;
  return (
    <>
      <div className="stats-form">
        <label className="stats-check"><input type="checkbox" checked={weighted} onChange={(e) => setWeighted(e.target.checked)} />С весами декластеризации</label>
        {weighted && <DeclusFields form={form} onChange={setForm} />}
        <RunButton busy={run.busy} onClick={() => run.start(() => runNscore(setId, component, weighted ? declusParams(form) : null, table))} />
      </div>
      <Results run={run}>
        {r && (
          <div className="stats-body">
            <Summary2
              rows={[
                ["Интервалов", String(r.count)],
                ["Диапазон значений", `${formatNumber(r.values[0])} … ${formatNumber(r.values[r.values.length - 1])}`],
                ["Оценки: среднее", formatNumber(Math.abs(mean) < 1e-9 ? 0 : mean, 3)],
                ["Оценки: σ", formatNumber(std, 3)],
                ["Границы хвостов", `${formatNumber(r.zmin)} … ${formatNumber(r.zmax)}`],
              ]}
            />
            <LineChart series={[{ label: "Таблица", x: r.values, y: r.scores }]} xLabel={`${component}`} yLabel="Нормальная оценка" />
          </div>
        )}
      </Results>
    </>
  );
}

interface ModelForm {
  on: boolean;
  nug: string;
  it: "spherical" | "exponential" | "gaussian";
  cc: string;
  hmaj: string;
  hmin: string;
  azi: string;
}

const DEFAULT_MODEL: ModelForm = { on: false, nug: "0", it: "spherical", cc: "1", hmaj: "", hmin: "", azi: "0" };

function modelParams(f: ModelForm, fallbackRange: number): VariogramModelParams | null {
  if (!f.on) return null;
  const hmaj = parseNumber(f.hmaj) ?? fallbackRange;
  return {
    nug: parseNumber(f.nug) ?? 0,
    structures: [{ it: f.it, cc: parseNumber(f.cc) ?? 1, hmaj, hmin: parseNumber(f.hmin), azi: parseNumber(f.azi) ?? 0 }],
  };
}

const OMNI: DirectionParams = { azimuth: 0, azimuth_tol: 90, bandwidth: null };
const FOUR: DirectionParams[] = [0, 45, 90, 135].map((azimuth) => ({ azimuth, azimuth_tol: 22.5, bandwidth: null }));

function VariogramPanel({ setId, component, table, lag0 }: PanelProps & { lag0: number }) {
  const [lag, setLag] = useState(String(lag0));
  const [nlag, setNlag] = useState("10");
  const [dirs, setDirs] = useState<"omni" | "four">("omni");
  const [standardize, setStandardize] = useState(false);
  const [model, setModel] = useState(DEFAULT_MODEL);
  const run = useRun<VariogramResult>();
  const r = run.result;
  const lagValue = parseNumber(lag) ?? lag0;
  const series: Series[] = [];
  r?.directions.forEach((d) => {
    const name = d.azimuth_tol >= 90 ? "Омни" : `${d.azimuth}°`;
    series.push({ label: name, x: d.dist, y: d.gamma });
    if (d.model_gamma) series.push({ label: `Модель ${name}`, x: r.lags, y: d.model_gamma, style: "dashed" });
  });
  return (
    <>
      <div className="stats-form">
        <Field label="Лаг" hint="Шаг по расстоянию, в единицах карты"><NumberInput value={lag} onChange={setLag} /></Field>
        <Field label="Лагов"><NumberInput value={nlag} onChange={setNlag} width={50} /></Field>
        <Field label="Направления">
          <select value={dirs} onChange={(e) => setDirs(e.target.value as "omni" | "four")}>
            <option value="omni">Омнинаправленная</option>
            <option value="four">0°, 45°, 90°, 135° (допуск 22,5°)</option>
          </select>
        </Field>
        <label className="stats-check"><input type="checkbox" checked={standardize} onChange={(e) => setStandardize(e.target.checked)} />Нормировать на дисперсию</label>
        <label className="stats-check"><input type="checkbox" checked={model.on} onChange={(e) => setModel({ ...model, on: e.target.checked })} />Модель</label>
        {model.on && (
          <>
            <Field label="Тип">
              <select value={model.it} onChange={(e) => setModel({ ...model, it: e.target.value as ModelForm["it"] })}>
                <option value="spherical">Сферическая</option>
                <option value="exponential">Экспоненциальная</option>
                <option value="gaussian">Гауссова</option>
              </select>
            </Field>
            <Field label="Нагет"><NumberInput value={model.nug} onChange={(nug) => setModel({ ...model, nug })} width={56} /></Field>
            <Field label="Порог (вклад)"><NumberInput value={model.cc} onChange={(cc) => setModel({ ...model, cc })} width={56} /></Field>
            <Field label="Ранг, большой" hint="Пусто — 5 лагов"><NumberInput value={model.hmaj} onChange={(hmaj) => setModel({ ...model, hmaj })} placeholder="авто" width={64} /></Field>
            <Field label="малый" hint="Пусто — равен большому"><NumberInput value={model.hmin} onChange={(hmin) => setModel({ ...model, hmin })} placeholder="авто" width={64} /></Field>
            <Field label="Азимут"><NumberInput value={model.azi} onChange={(azi) => setModel({ ...model, azi })} width={50} /></Field>
          </>
        )}
        <RunButton
          busy={run.busy}
          onClick={() =>
            run.start(() =>
              runVariogram(setId, component, {
                lag: lagValue,
                nlag: Math.round(parseNumber(nlag) ?? 10),
                directions: dirs === "omni" ? [OMNI] : FOUR,
                standardize,
                model: modelParams(model, 5 * lagValue),
              }, table),
            )
          }
        />
      </div>
      <Results run={run}>
        {r && (
          <div className="stats-body">
            <LineChart series={series} xLabel="Расстояние" yLabel={`γ(h) ${component}`} hLine={{ value: standardize ? 1 : r.variance, label: "дисперсия" }} />
            <div className="table-wrap stats-pairs">
              <table>
                <thead>
                  <tr>
                    <th>Лаг</th>
                    {r.directions.map((d) => (
                      <th key={d.azimuth + ":" + d.azimuth_tol}>Пар, {d.azimuth_tol >= 90 ? "омни" : `${d.azimuth}°`}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {r.lags.map((h, i) => (
                    <tr key={h}>
                      <td>{formatNumber(h)}</td>
                      {r.directions.map((d) => (
                        <td key={d.azimuth + ":" + d.azimuth_tol} className={d.npairs[i] < 30 ? "stats-few" : undefined}>{d.npairs[i]}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small">Меньше 30 пар в лаге — значение ненадёжно. Интервалов: {r.count}, дисперсия {formatNumber(r.variance, 4)}.</p>
            </div>
          </div>
        )}
      </Results>
    </>
  );
}

function MapPanel({ setId, component, table, lag0 }: PanelProps & { lag0: number }) {
  const [step, setStep] = useState(String(lag0));
  const [n, setN] = useState("8");
  const [minnp, setMinnp] = useState("5");
  const [standardize, setStandardize] = useState(false);
  const run = useRun<VariogramMapResult>();
  const r = run.result;
  let scale: ColorScale | null = null;
  if (r) {
    const finite = r.gamma.flat().filter((v): v is number => v !== null);
    if (finite.length) scale = new ColorScale(Math.min(...finite), Math.max(...finite), false);
  }
  return (
    <>
      <div className="stats-form">
        <Field label="Шаг по X и Y" hint="Размер ячейки карты, в единицах карты"><NumberInput value={step} onChange={setStep} /></Field>
        <Field label="Ячеек от центра"><NumberInput value={n} onChange={setN} width={50} /></Field>
        <Field label="Мин. пар"><NumberInput value={minnp} onChange={setMinnp} width={50} /></Field>
        <label className="stats-check"><input type="checkbox" checked={standardize} onChange={(e) => setStandardize(e.target.checked)} />Нормировать на дисперсию</label>
        <RunButton
          busy={run.busy}
          onClick={() => {
            const d = parseNumber(step) ?? lag0;
            const k = Math.round(parseNumber(n) ?? 8);
            run.start(() => runVariogramMap(setId, component, { nxlag: k, nylag: k, dxlag: d, dylag: d, minnp: Math.round(parseNumber(minnp) ?? 5), standardize }, table));
          }}
        />
      </div>
      <Results run={run}>
        {r && (scale ? (
          <div className="stats-body">
            <Heatmap x={r.x} y={r.y} values={r.gamma} scale={scale} xLabel="ΔX" yLabel="ΔY" />
            <p className="muted small">Цвет — γ. Пустые ячейки: пар меньше порога. Интервалов: {r.count}. Карта симметрична относительно центра.</p>
          </div>
        ) : (
          <p className="muted small">Ни в одной ячейке нет достаточно пар: увеличьте шаг или уменьшите порог.</p>
        ))}
      </Results>
    </>
  );
}


const STATISTICS: [BootstrapStatistic, string][] = [
  ["mean", "Среднее"], ["median", "Медиана"], ["std", "Станд. отклонение"], ["cv", "Коэф. вариации"], ["p10", "P10"], ["p90", "P90"],
];
type BootstrapStatistic = "mean" | "median" | "std" | "cv" | "p10" | "p90";

function BootstrapPanel({ setId, component, table }: PanelProps) {
  const [statistic, setStatistic] = useState<BootstrapStatistic>("mean");
  const [nBoot, setNBoot] = useState("1000");
  const [confidence, setConfidence] = useState("0.9");
  const [weighted, setWeighted] = useState(false);
  const [form, setForm] = useState(DEFAULT_DECLUS);
  const run = useRun<BootstrapResult>();
  const r = run.result;
  const title = STATISTICS.find(([id]) => id === r?.statistic)?.[1] ?? "";
  const width = r ? ((r.ci_high - r.ci_low) / (Math.abs(r.observed) || 1)) * 100 : 0;
  return (
    <>
      <div className="stats-form">
        <Field label="Статистика">
          <select value={statistic} onChange={(e) => setStatistic(e.target.value as BootstrapStatistic)}>
            {STATISTICS.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </Field>
        <Field label="Реализаций" hint="Сколько раз пересэмплировать данные (10–5000)"><NumberInput value={nBoot} onChange={setNBoot} width={60} /></Field>
        <Field label="Доверие">
          <select value={confidence} onChange={(e) => setConfidence(e.target.value)}>
            <option value="0.8">80 %</option>
            <option value="0.9">90 %</option>
            <option value="0.95">95 %</option>
            <option value="0.99">99 %</option>
          </select>
        </Field>
        <label className="stats-check" title="Интервалы выбираются с вероятностью, пропорциональной весу декластеризации"><input type="checkbox" checked={weighted} onChange={(e) => setWeighted(e.target.checked)} />С весами декластеризации</label>
        {weighted && <DeclusFields form={form} onChange={setForm} />}
        <RunButton
          busy={run.busy}
          onClick={() =>
            run.start(() =>
              runBootstrap(setId, component, {
                statistic,
                n_boot: Math.round(parseNumber(nBoot) ?? 1000),
                confidence: Number(confidence),
                declus: weighted ? declusParams(form) : null,
                seed: null,
              }, table),
            )
          }
        />
      </div>
      <Results run={run}>
        {r && (
          <div className="stats-body">
            <Summary2
              rows={[
                ["Интервалов", String(r.count)],
                [`${title}: по данным`, formatNumber(r.observed, 4)],
                [`${title}: бутстрап`, formatNumber(r.mean, 4)],
                ["Станд. ошибка", formatNumber(r.std, 3)],
                [`Интервал ${Math.round(r.confidence * 100)} %`, `${formatNumber(r.ci_low, 4)} … ${formatNumber(r.ci_high, 4)}`],
                ["Ширина интервала", `${formatNumber(width, 3)} % от значения`],
              ]}
            />
            <Histogram
              edges={r.edges}
              counts={r.counts}
              xLabel={`${title} ${component}`}
              marks={[
                { value: r.observed, label: "данные" },
                { value: r.ci_low, label: "нижняя", dashed: true },
                { value: r.ci_high, label: "верхняя", dashed: true },
              ]}
            />
          </div>
        )}
      </Results>
    </>
  );
}

type LocalMap = "mean" | "std" | "p10" | "p90" | "prob_exceed";
const LOCAL_MAPS: [LocalMap, string][] = [
  ["mean", "Среднее"], ["std", "Станд. отклонение"], ["p10", "P10"], ["p90", "P90"], ["prob_exceed", "Вероятность ≥ порога"],
];

function finiteRange(grids: Grid[]): [number, number] | null {
  const values = grids.flatMap((g) => g.flat()).filter((v): v is number => v !== null);
  return values.length ? [Math.min(...values), Math.max(...values)] : null;
}

function LocalPanel({ setId, component, table }: PanelProps) {
  const [n, setN] = useState("30");
  const [nreal, setNreal] = useState("50");
  const [k, setK] = useState("8");
  const [power, setPower] = useState("2");
  const [radius, setRadius] = useState("");
  const [threshold, setThreshold] = useState("");
  const [which, setWhich] = useState<LocalMap>("mean");
  const run = useRun<LocalResult>();
  const r = run.result;
  const grid: Grid | null = r ? (which === "prob_exceed" ? r.prob_exceed : r[which]) : null;
  // mean, P10 and P90 share one scale so they can be compared; the others have their own
  const range = !r || !grid ? null : which === "prob_exceed" ? ([0, 1] as [number, number]) : which === "std" ? finiteRange([r.std]) : finiteRange([r.mean, r.p10, r.p90]);
  const scale = range ? new ColorScale(range[0], range[1], false) : null;
  return (
    <>
      <div className="stats-form">
        <Field label="Ячеек по стороне"><NumberInput value={n} onChange={setN} width={50} /></Field>
        <Field label="Реализаций" hint="Сколько раз пересэмплировать скважины (5–300)"><NumberInput value={nreal} onChange={setNreal} width={56} /></Field>
        <Field label="Соседей" hint="Сколько ближайших интервалов участвует в оценке"><NumberInput value={k} onChange={setK} width={46} /></Field>
        <Field label="Степень" hint="Степень обратного расстояния"><NumberInput value={power} onChange={setPower} width={46} /></Field>
        <Field label="Радиус" hint="Поиск соседей; пусто — 25 % протяжённости плана. Ячейки без данных в радиусе остаются пустыми"><NumberInput value={radius} onChange={setRadius} placeholder="авто" width={64} /></Field>
        <Field label="Порог" hint="Если задан — считается вероятность превысить его"><NumberInput value={threshold} onChange={setThreshold} placeholder="нет" width={64} /></Field>
        <RunButton
          busy={run.busy}
          onClick={() => {
            const cells = Math.round(parseNumber(n) ?? 30);
            run.start(() =>
              runLocalUncertainty(setId, component, {
                nx: cells, ny: cells, nreal: Math.round(parseNumber(nreal) ?? 50), k: Math.round(parseNumber(k) ?? 8),
                power: parseNumber(power) ?? 2, radius: parseNumber(radius), threshold: parseNumber(threshold), seed: null,
              }, table),
            );
            setWhich((w) => (w === "prob_exceed" && parseNumber(threshold) === null ? "mean" : w));
          }}
        />
      </div>
      <Results run={run}>
        {r && (
          <div className="stats-body">
            <div>
              <div className="tabs" role="tablist" aria-label="Карта">
                {LOCAL_MAPS.filter(([id]) => id !== "prob_exceed" || r.prob_exceed).map(([id, name]) => (
                  <button key={id} role="tab" aria-selected={which === id} className={which === id ? "active tab" : "tab"} onClick={() => setWhich(id)}>{name}</button>
                ))}
              </div>
              {grid && scale ? (
                <Heatmap x={r.x} y={r.y} values={grid} scale={scale} xLabel="X" yLabel="Y" size={340} format={coordFormat(r.x)} />
              ) : (
                <p className="muted small">В радиусе поиска нет данных: увеличьте радиус.</p>
              )}
            </div>
            <p className="muted small stats-note">
              Скважин: {r.holes}, интервалов: {r.count}, реализаций: {r.nreal}, радиус поиска {formatNumber(r.radius)}.
              Каждая реализация — случайная выборка скважин с возвратом и оценка обратными расстояниями на сетке;
              карта показывает, насколько оценка зависит от того, какие скважины пробурены.
              Пустые ячейки — нет данных в радиусе.
            </p>
          </div>
        )}
      </Results>
    </>
  );
}

export function StatsPage({
  setId,
  refreshKey,
  summary,
  holes,
  view,
  onViewChange,
}: {
  setId: number;
  refreshKey: number;
  summary: Summary | null;
  holes: Hole[];
  view: StatsView;
  onViewChange: (next: StatsView) => void;
}) {
  const tables = useSampleTables(setId, refreshKey);
  const table = effectiveTable(tables, view.table);
  const [fields, setFields] = useState<NumericField[] | null>(null);
  useEffect(() => {
    if (!tables) return;
    let cancelled = false;
    getNumericFields(setId, table).then((f) => !cancelled && setFields(f)).catch(() => !cancelled && setFields([]));
    return () => {
      cancelled = true;
    };
  }, [setId, table, tables, refreshKey]);
  void summary;
  const components = (fields ?? []).filter((f) => f.kind === "component").map((f) => ({ name: f.name, unit: f.unit }));
  const component = components.find((c) => c.name === view.component)?.name ?? components[0]?.name ?? null;
  const lag0 = defaultLag(planSpan(holes));
  if (!tables || !fields) return <p className="muted small">Загрузка…</p>;
  if (component === null) {
    return <p className="muted small">В наборе нет таблиц опробования с числами: импортируйте опробование.</p>;
  }
  const unit = components.find((c) => c.name === component)?.unit;
  // A new set, table, component or data: drop old results instead of showing them for something else.
  const key = `${setId}:${table}:${component}:${refreshKey}`;
  const body = { setId, component, table };
  return (
    <main className="page stats-page">
      <div className="stats-head">
        <div className="tabs" role="tablist" aria-label="Вид анализа">
          {ANALYSES.map(([id, title]) => (
            <button key={id} role="tab" aria-selected={view.analysis === id} className={view.analysis === id ? "active tab" : "tab"} onClick={() => onViewChange({ ...view, analysis: id })}>
              {title}
            </button>
          ))}
        </div>
        {tables.tables.length > 1 && (
          <label className="stats-field">
            <span>Таблица</span>
            <select value={table ?? ""} onChange={(e) => onViewChange({ ...view, table: e.target.value, component: null })} aria-label="Таблица проб">
              {tables.tables.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
            </select>
          </label>
        )}
        <label className="stats-field">
          <span>Компонент</span>
          <select value={component} onChange={(e) => onViewChange({ ...view, component: e.target.value })}>
            {components.map((c) => (
              <option key={c.name} value={c.name}>{c.name}{c.unit ? `, ${c.unit}` : ""}</option>
            ))}
          </select>
        </label>
        <span className="muted small" title="Расчёт идёт по серединам интервалов опробования в плане (X, Y)">
          {unit ? `${unit} · ` : ""}по серединам интервалов, в плане
        </span>
      </div>
      <div key={key}>
        {/* all panels stay mounted, so switching the analysis keeps the results */}
        <section hidden={view.analysis !== "declus"}><DeclusPanel {...body} /></section>
        <section hidden={view.analysis !== "nscore"}><NscorePanel {...body} /></section>
        <section hidden={view.analysis !== "variogram"}><VariogramPanel {...body} lag0={lag0} /></section>
        <section hidden={view.analysis !== "map"}><MapPanel {...body} lag0={lag0} /></section>
        <section hidden={view.analysis !== "bootstrap"}><BootstrapPanel {...body} /></section>
        <section hidden={view.analysis !== "local"}><LocalPanel {...body} /></section>
      </div>
    </main>
  );
}
