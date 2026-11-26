/**
 * The two curves «тоннаж — содержание» of the two reports on one chart (ТЗ 7.35 Д-37; ADR 0027 решение 26): the cut-off on the axis, T, ḡ or M of the chosen element
 * above it (the switch), A — a solid line, B — a dashed one, the tooltip of the point under the cursor for both. The curves have their own cut-offs: each line is
 * drawn on its own. A point with no value breaks the line — a gap is not 0; a report without a curve is told in words. The shared frame of the charts
 * (`charts/core`), the brand colours; PNG, SVG and the CSV of the data — the toolbar of the charts. Presentation only.
 */
import { useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { XAxis, YAxis } from "../../charts/core/Axes";
import { domainOf, formatNumber, makeScale } from "../../charts/core/axisScale";
import css from "../../charts/core/Chart.module.css";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { csvText } from "../../charts/core/csv";
import { seriesColor } from "../../charts/core/palette";
import { plotBox, PlotFrame } from "../../charts/core/PlotFrame";
import { nearestIndex, pathOf, pieces } from "../../charts/gradetonnage/model";
import type { CompareAnswer } from "./answers";
import { elementOf, hasAny, METRIC_TITLES, noCurveText, seriesCsvRows, seriesOf, type CompareSeries, type Metric } from "./compareModel";
import panel from "./Panel.module.css";

const W = 640;
const H = 340;

export interface CompareChartProps {
  answer: CompareAnswer;
  element: string | null;
  metric: Metric;
  onElement?: (name: string) => void;
  onMetric?: (m: Metric) => void;
}

const METRICS: readonly { id: Metric; label: string }[] = [
  { id: "tonnes", label: "Тоннаж T" },
  { id: "grade", label: "Содержание ḡ" },
  { id: "metal", label: "Металл M" },
];

export function CompareChart({ answer, element, metric, onElement, onMetric }: CompareChartProps) {
  const svg = useRef<SVGSVGElement | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const el = elementOf(answer, element);
  const box = useMemo(() => plotBox(W, H, { left: 76, right: 20, top: 16, bottom: 46 }), []);
  const all = [seriesOf("a", `A — ${answer.a.name}`, answer.curves.a, metric, el), seriesOf("b", `B — ${answer.b.name}`, answer.curves.b, metric, el)];
  const series = all.filter((s): s is CompareSeries => s !== null);
  const drawn = series.filter(hasAny);
  const field = answer.curves.a?.field || answer.curves.b?.field || "";
  const xs = drawn.flatMap((s) => [...s.xs]);
  const ys = drawn.flatMap((s) => [...s.ys]);
  const xDom = domainOf(xs, "linear", 0);
  const yDom = domainOf(metric === "grade" ? ys : [0, ...ys], "linear", 0.06, metric === "grade" ? undefined : 0);
  const none = noCurveText(answer);
  const names = Object.keys((answer.curves.a ?? answer.curves.b)?.elements ?? {});
  const title = `${METRIC_TITLES[metric]}${metric === "tonnes" || !el ? "" : `, ${el}`}`;
  const label = `Две кривые «тоннаж — содержание» отчётов A и B${field ? ` по полю ${field}` : ""}`;

  const bar = (
    <div className={panel.bar}>
      {names.length > 1 && (
        <label>
          Элемент
          <select value={el ?? ""} aria-label="Элемент кривых" data-testid="cmp-curve-element" onChange={(e) => onElement?.(e.target.value)}>
            {names.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      )}
      <span className={panel.seg} role="group" aria-label="Что показывать на графике">
        {METRICS.map((m) => (
          <button key={m.id} type="button" aria-pressed={metric === m.id} data-testid={`cmp-metric-${m.id}`} onClick={() => onMetric?.(m.id)}>
            {m.label}
          </button>
        ))}
      </span>
      {none && (
        <span className={panel.note} data-testid="cmp-no-curve">
          {none}
        </span>
      )}
    </div>
  );
  if (!xDom || !yDom) {
    return (
      <div className={panel.chartBox} data-testid="cmp-chart" data-series={0}>
        {bar}
        <p className={panel.note} data-testid="cmp-chart-empty">
          {series.length ? "Кривая не построена: нет значения ни в одной точке." : "Кривых нет: оба отчёта построены без кривой."}
        </p>
      </div>
    );
  }
  const sx = makeScale("linear", xDom, box.x0, box.x1);
  const sy = makeScale("linear", yDom, box.y1, box.y0);
  const pick = (e: RPointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / (r.width || 1)) * W;
    setHover(px < box.x0 || px > box.x1 ? null : px);
  };
  const at = hover === null ? null : sx.invert(hover);
  const tip =
    at === null
      ? []
      : [
          `Борт ${formatNumber(at, 4)}`,
          ...drawn.map((s) => {
            const i = nearestIndex(s.xs, at);
            const v = s.ys[i];
            return `${s.id === "a" ? "A" : "B"}: ${v === null || v === undefined ? "нет значения" : formatNumber(v, 5)} (борт ${formatNumber(s.xs[i], 4)})`;
          }),
        ];
  const tipX = at === null ? 0 : (sx.map(at) ?? 0);
  return (
    <div className={panel.chartBox} data-testid="cmp-chart" data-series={drawn.length} data-metric={metric} data-element={el ?? undefined}>
      {bar}
      <PlotFrame ref={svg} box={box} label={label} xTitle={`Борт${field ? `: ${field}` : ""}`} yTitle={title} onPointerMove={pick} onPointerLeave={() => setHover(null)}>
        <YAxis scale={sy} box={box} />
        <XAxis scale={sx} box={box} count={7} />
        {drawn.map((s) => {
          const color = seriesColor(s.id === "a" ? 0 : 1);
          return (
            <g key={s.id} data-series={s.id} fill={color}>
              {pieces(s.xs, s.ys, (v) => sx.map(v), (v) => sy.map(v)).map((p, i) => (
                <path key={i} d={pathOf(p)} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeDasharray={s.id === "b" ? "7 4" : undefined} />
              ))}
            </g>
          );
        })}
        {at !== null && (
          <g pointerEvents="none" data-hover>
            <line className={css.mark} x1={tipX} x2={tipX} y1={box.y0} y2={box.y1} />
            <g className={css.tip} transform={`translate(${Math.min(tipX + 10, box.x1 - 250)} ${box.y0 + 6})`}>
              <rect width={244} height={14 + tip.length * 15} rx={4} />
              {tip.map((t, i) => (
                <text key={i} x={8} y={16 + i * 15}>
                  {t}
                </text>
              ))}
            </g>
          </g>
        )}
      </PlotFrame>
      <div className={panel.bar} data-testid="cmp-legend">
        {drawn.map((s) => (
          <span key={s.id} className={panel.note}>
            <svg width={26} height={8} aria-hidden="true">
              <line x1={0} x2={26} y1={4} y2={4} stroke={seriesColor(s.id === "a" ? 0 : 1)} strokeWidth={2} strokeDasharray={s.id === "b" ? "7 4" : undefined} />
            </svg>{" "}
            {s.label}
          </span>
        ))}
      </div>
      <ChartToolbar svgRef={svg} fileName="Сравнение кривых отчётов" csv={() => csvText(["Отчёт", "Борт", title], seriesCsvRows(drawn))} csvLabel="CSV кривых" />
    </div>
  );
}
