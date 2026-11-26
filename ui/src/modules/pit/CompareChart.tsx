/**
 * The two curves of the comparison of two pit scenarios (ТЗ 8.11.5; ADR 0028 решение 27): RF on the axis, the quantity (NPV of the best or the worst case, CF, the ore…) above it;
 * A — a solid line, B — a dashed one, the tooltip of the shell under the cursor for both. A shell only one scenario has is a point of its curve only (the other curve has no point
 * there: a gap, not 0); a value that is not known breaks the line. The shared frame of the charts (`charts/core`), the brand colours; PNG, SVG and the CSV of the data — the toolbar.
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
import { type CompareAnswer, type ShellKey } from "./resultsAnswers";
import { curveOf, curveRows, METRICS, metricInfo, rfText, val, type Curve } from "./resultsModel";
import mod from "./Pit.module.css";
import res from "./Results.module.css";

const W = 640;
const H = 320;

export interface CompareChartProps {
  answer: CompareAnswer;
  metric: ShellKey;
  onMetric?: (k: ShellKey) => void;
}

export function CompareChart({ answer, metric, onMetric }: CompareChartProps) {
  const svg = useRef<SVGSVGElement | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const box = useMemo(() => plotBox(W, H, { left: 84, right: 20, top: 16, bottom: 46 }), []);
  const info = metricInfo(metric);
  const curves: Curve[] = [curveOf(answer, "a", metric), curveOf(answer, "b", metric)];
  const drawn = curves.filter((c) => c.ys.some((y) => y !== null && Number.isFinite(y)));
  const xDom = domainOf(drawn.flatMap((c) => c.xs), "linear", 0.04);
  const yDom = domainOf(drawn.flatMap((c) => c.ys), "linear", 0.06);
  const title = `${info.label}, ${info.unit}`;

  const bar = (
    <div className={res.tools}>
      <span className={mod.seg} role="group" aria-label="Что показывать на графике">
        {METRICS.map((m) => (
          <button key={m.key} type="button" aria-pressed={metric === m.key} data-testid={`pit-cmp-metric-${m.key}`} onClick={() => onMetric?.(m.key)}>
            {m.label}
          </button>
        ))}
      </span>
    </div>
  );
  if (!xDom || !yDom) {
    return (
      <div className={mod.chartBox} data-testid="pit-cmp-chart" data-series={0}>
        {bar}
        <p className={res.empty} data-testid="pit-cmp-chart-empty">
          График не построен: у оболочек нет значения «{info.label}».
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
          `RF ${formatNumber(at, 4)}`,
          ...drawn.map((c) => {
            const i = nearestIndex(c.xs, at);
            return `${c.id.toUpperCase()}: ${val(c.ys[i])} (RF ${rfText(c.xs[i])})`;
          }),
        ];
  const tipX = at === null ? 0 : (sx.map(at) ?? 0);
  return (
    <div className={mod.chartBox} data-testid="pit-cmp-chart" data-series={drawn.length} data-metric={metric}>
      {bar}
      <PlotFrame ref={svg} box={box} label={`Две кривые «${info.label}» сценариев A и B по коэффициенту цены RF`} xTitle="Коэффициент цены RF" yTitle={title} onPointerMove={pick} onPointerLeave={() => setHover(null)}>
        <YAxis scale={sy} box={box} />
        <XAxis scale={sx} box={box} count={7} />
        {drawn.map((c) => {
          const color = seriesColor(c.id === "a" ? 0 : 1);
          return (
            <g key={c.id} data-series={c.id} fill={color}>
              {pieces(c.xs, c.ys, (v) => sx.map(v), (v) => sy.map(v)).map((p, i) => (
                <path key={i} d={pathOf(p)} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeDasharray={c.id === "b" ? "7 4" : undefined} />
              ))}
              {c.xs.map((x, i) => {
                const px = sx.map(x);
                const py = c.ys[i] === null ? null : sy.map(c.ys[i] as number);
                return px === null || py === null ? null : <circle key={i} cx={px} cy={py} r={2.5} />;
              })}
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
      <div className={mod.legend} data-testid="pit-cmp-legend">
        {drawn.map((c) => (
          <span key={c.id} className={mod.legendItem}>
            <svg width={26} height={8} aria-hidden="true">
              <line x1={0} x2={26} y1={4} y2={4} stroke={seriesColor(c.id === "a" ? 0 : 1)} strokeWidth={2} strokeDasharray={c.id === "b" ? "7 4" : undefined} />
            </svg>{" "}
            {c.label}
          </span>
        ))}
      </div>
      <ChartToolbar svgRef={svg} fileName="Сравнение сценариев карьера" csv={() => csvText(["Сценарий", "RF", title], curveRows(drawn))} csvLabel="CSV кривых" />
    </div>
  );
}
