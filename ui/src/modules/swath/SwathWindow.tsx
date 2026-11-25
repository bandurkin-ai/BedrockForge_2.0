/**
 * The window «Swath plot · ‹название›» (ТЗ 7.30, 7.35 Д-01; ADR 0019 §6, §11; перенос `SwathPanel` BF): floating,
 * not modal, one per swath, several at once. On the left: the direction (X, Y, Z, по углам) and the three angles
 * of the box as numbers, the width (a refusal of the core — over 2000 swaths — is shown at it), the anchor, the
 * series, the 3D view; on the right: the export, the plot, the range «с … по …» with sliders, the notes and the
 * table with Δ from the first series. An edit computes again 300 ms later (an overtaken answer is dropped) and is
 * saved 400 ms later; an edit of data computes again (Т7.30-5). The swath under the cursor is lit in 3D. While a
 * handle of the box is dragged in 3D (Д-01) the fields show its angles and the plot stays as it was, dimmed; the
 * release gives the window the new settings (`stats.swath`), and it computes once.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ChartWindow } from "../../charts/ChartWindow";
import { formatNumber } from "../../charts/core/axisScale";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { csvText } from "../../charts/core/csv";
import chart from "../../charts/core/Chart.module.css";
import { NumberInput } from "../../charts/SampleFilterForm";
import { SwathChart } from "../../charts/swath/SwathChart";
import { useApp } from "../../shell/AppContext";
import { errorText } from "../../shell/props";
import { viewStore } from "../scene/bind";
import { shownSwaths, SWATHS_KEY } from "../scene/viewSync";
import { loadSources, swathCalculator, swathError, type SwathResult, type SwathSources } from "./api";
import { anglesOf, DIRECTIONS, seriesNote, shownRange, swathCsv, swathProblem, swathRequest, withAngles, withDirection, type SwathSettings } from "./model";
import { useSavedSwath } from "./saver";
import { SeriesList } from "./SeriesList";
import { dragStore, onPushed, selectSwath, setHover, setLive, swathsStore } from "./state";
import { SwathTable } from "./SwathTable";
import css from "./Swath.module.css";

export interface SwathWindowProps {
  winId: string;
  /** the saved swath; null — not saved (a project open only for reading, a failed save) */
  swathId: number | null;
  settings: SwathSettings;
  readOnly?: boolean;
  saveError?: string | null;
}

/** The swaths shown: sliders and «с … по …» (numbers from 1); all — no range. */
function RangeFilter({ count, range, onChange }: { count: number; range: [number, number]; onChange: (next: [number, number] | null) => void }) {
  const all = range[0] === 0 && range[1] === count - 1;
  const set = (a: number, b: number) => {
    const from = Math.min(Math.max(0, Math.round(a)), count - 1);
    const to = Math.min(Math.max(from, Math.round(b)), count - 1);
    onChange(from === 0 && to === count - 1 ? null : [from, to]);
  };
  return (
    <div className={css.range} role="group" aria-label="Диапазон полос">
      <span className={chart.muted}>Полосы с</span>
      <input type="number" min={1} max={count} value={range[0] + 1} aria-label="Первая полоса" onChange={(e) => set(Number(e.target.value) - 1, range[1])} />
      <input type="range" min={0} max={count - 1} value={range[0]} aria-label="Первая полоса (ползунок)" onChange={(e) => set(Number(e.target.value), Math.max(range[1], Number(e.target.value)))} />
      <input type="range" min={0} max={count - 1} value={range[1]} aria-label="Последняя полоса (ползунок)" onChange={(e) => set(Math.min(range[0], Number(e.target.value)), Number(e.target.value))} />
      <span className={chart.muted}>по</span>
      <input type="number" min={1} max={count} value={range[1] + 1} aria-label="Последняя полоса" onChange={(e) => set(range[0], Number(e.target.value) - 1)} />
      <span className={chart.muted}>из {count}</span>
      <button type="button" disabled={all} onClick={() => onChange(null)}>
        Все
      </button>
    </div>
  );
}

const ANGLES: ["azimuth" | "dip" | "roll", string, string][] = [
  ["azimuth", "Азимут, °", "От севера по часовой, 0–360°"],
  ["dip", "Наклон, °", "−90…+90°; отрицательный — ось полос идёт вниз"],
  ["roll", "Поворот, °", "Вокруг оси полос, −180…+180°"],
];

export function SwathWindow(props: SwathWindowProps) {
  const app = useApp();
  const { swathId } = props;
  const [s, setS] = useState<SwathSettings>(props.settings);
  const [sources, setSources] = useState<SwathSources | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [result, setResult] = useState<SwathResult | null>(null);
  const [error, setError] = useState<{ text: string; param: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [hover, setHoverLocal] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const getRev = () => swathsStore.get().rev;
  const rev = useSyncExternalStore(swathsStore.subscribe, getRev, getRev);
  const getShown = () => swathId !== null && shownSwaths(viewStore.get()[SWATHS_KEY]).includes(swathId);
  const shown3d = useSyncExternalStore(viewStore.subscribe, getShown, getShown);
  const getDrag = () => dragStore.get().drag;
  const drag = useSyncExternalStore(dragStore.subscribe, getDrag, getDrag);
  const dragged = drag !== null && drag.id === swathId ? drag : null;
  const set = (patch: Partial<SwathSettings>) => setS((prev) => ({ ...prev, ...patch }));

  useEffect(() => (swathId !== null ? onPushed(swathId, setS) : undefined), [swathId]);
  useEffect(() => {
    let alive = true;
    loadSources()
      .then((x) => {
        if (!alive) return;
        setSources(x);
        setSourcesError(null);
      })
      .catch((e: unknown) => alive && setSourcesError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [rev]);

  const saved = useSavedSwath(props, s, setS);

  const calc = useMemo(
    () =>
      swathCalculator(
        (r) => {
          setResult(r);
          setError(null);
        },
        (e) => setError(swathError(e)),
        setBusy,
      ),
    [],
  );
  useEffect(() => () => calc.stop(), [calc]);
  const problem = swathProblem(s);
  const body = useMemo(() => JSON.stringify(swathRequest(s)), [s]);
  useEffect(() => {
    if (!problem) calc.request({ body: { settings: JSON.parse(body) as object }, rev });
  }, [calc, body, problem, rev]);

  // the 3D draws what the window has; the swath under the cursor is lit there
  useEffect(() => {
    if (swathId !== null) setLive(swathId, { settings: s, result, error });
  }, [swathId, s, result, error]);
  useEffect(() => () => void (swathId !== null && (setLive(swathId, null), setHover(swathId, null))), [swathId]);
  const hoverTo = (k: number | null) => {
    setHoverLocal(k);
    if (swathId !== null) setHover(swathId, k);
  };

  const fits = !!result && result.series.length === s.series.length;
  const range = result ? shownRange(s.range, result.count) : ([0, 0] as [number, number]);
  const angles = dragged ?? anglesOf(s);
  const widthError = error?.param === "width" || error?.param === "anchor" ? error.text : null;
  const seriesError = error?.param === "series" ? error.text : null;
  const generalError = error && !widthError && !seriesError ? error.text : null;

  const settingsForm = (
    <>
      <fieldset>
        <legend>Полосы</legend>
        <div className={chart.seg} role="group" aria-label="Направление полос">
          {DIRECTIONS.map(([d, t]) => (
            <button key={d} type="button" aria-pressed={s.direction === d} onClick={() => setS((prev) => withDirection(prev, d))}>
              {t}
            </button>
          ))}
        </div>
        <div className={css.angles}>
          {ANGLES.map(([k, t, hint]) => (
            <label key={k} title={hint}>
              {t}
              <NumberInput label={t} value={angles[k]} title={hint} placeholder="0" onChange={(v) => v !== null && setS((prev) => withAngles(prev, { [k]: v }))} />
            </label>
          ))}
        </div>
        {(s.direction === "x" || s.direction === "y") && <p className={chart.muted}>Поворот короба делает направление «по углам».</p>}
        <div className={chart.pair}>
          <label>
            Ширина, м
            <NumberInput label="Ширина полосы" value={s.width} placeholder={fits ? `авто: ${formatNumber(result!.width, 5)}` : "авто"} title="Пусто — целые родительские блоки первой модели (не больше 50 полос) или круглое число около 1/25 габарита" onChange={(v) => set({ width: v !== null && v > 0 ? v : null, range: null })} />
          </label>
          <label>
            Привязка
            <NumberInput label="Привязка полос" value={s.anchor} placeholder="от края" title="Через эту координату вдоль оси полос проходит граница полос" onChange={(v) => set({ anchor: v, range: null })} />
          </label>
        </div>
        {widthError && (
          <p className={chart.error} role="alert" data-testid="swath-width-error">
            {widthError}
          </p>
        )}
        {fits && (
          <p className={chart.muted}>
            {result!.count} полос по {formatNumber(result!.width, 5)} м{result!.block ? ` (блок ${formatNumber(result!.block, 5)} м)` : ""}. Блок и проба относятся к полосе по своему центру.
          </p>
        )}
      </fieldset>
      <SeriesList settings={s} sources={sources} answers={result?.series} onChange={setS} />
      {seriesError && (
        <p className={chart.error} role="alert">
          {seriesError}
        </p>
      )}
      {sourcesError && <p className={chart.muted}>Источники не прочитаны: {sourcesError}</p>}
      <fieldset>
        <legend>3D</legend>
        <label className={chart.check} title={swathId === null ? "Сначала swath plot нужно сохранить" : "Кубоид на полосах и график над ним в 3D-окне"}>
          <input type="checkbox" checked={shown3d} disabled={swathId === null} onChange={(e) => swathId !== null && void app.run("stats.swath_3d", { swath_id: swathId, visible: e.target.checked })} /> Полосы в 3D
        </label>
        <label className={chart.check}>
          <input type="checkbox" checked={s.view.chart3d} disabled={!shown3d} onChange={(e) => set({ view: { ...s.view, chart3d: e.target.checked } })} /> График над полосами
        </label>
        <label>
          Высота графика: {Math.round(s.view.height3d * 100)} % кубоида
          <input type="range" min={0.1} max={1} step={0.05} value={s.view.height3d} disabled={!shown3d || !s.view.chart3d} aria-label="Высота графика в 3D" onChange={(e) => set({ view: { ...s.view, height3d: Number(e.target.value) } })} />
        </label>
      </fieldset>
    </>
  );

  const fileName = saved.name;
  const toolbar = (
    <ChartToolbar
      svgRef={svgRef}
      fileName={fileName}
      disabled={!fits}
      csvLabel="CSV полос"
      csv={() => {
        if (!result || !fits) return null;
        const t = swathCsv(result, range);
        return csvText(t.header, t.rows);
      }}
    />
  );
  const look = s.series.map((x) => ({ color: x.color, line: x.line, bars: x.bars }));
  return (
    <div className={css.host} onPointerDownCapture={() => swathId !== null && selectSwath(swathId)} onFocusCapture={() => swathId !== null && selectSwath(swathId)} data-testid="swath-window">
      <ChartWindow
        settings={settingsForm}
        toolbar={toolbar}
        busy={busy}
        error={problem ?? generalError}
        empty={problem ?? "Расчёт…"}
        note={saved.note}
        chart={
          fits && (
            <div className={dragged ? css.dragged : undefined} data-dragged={dragged ? "true" : undefined}>
              <SwathChart ref={svgRef} result={result!} look={look} range={range} hover={hover} onHover={hoverTo} />
            </div>
          )
        }
        footer={
          fits && (
            <>
              {result!.count > 1 && <RangeFilter count={result!.count} range={range} onChange={(next) => set({ range: next })} />}
              {result!.notes.map((n) => (
                <p key={n} className={chart.muted}>
                  {n}
                </p>
              ))}
              <SwathTable result={result!} range={range} hover={hover} onHover={hoverTo} />
              <p className={chart.muted}>{result!.series.map(seriesNote).join(" ")}</p>
            </>
          )
        }
      />
    </div>
  );
}
