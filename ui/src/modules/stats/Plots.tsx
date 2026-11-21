/**
 * The charts of the tab «Статистика» on the base of the chart windows (`charts/core`, ADR 0019 §11): a curve
 * y(x) (declustering «размер ячейки → среднее», the normal score transform) with a horizontal reference and a
 * marked point, and the histogram of the bootstrap realizations with vertical marks. A missing value breaks the
 * line — never drawn as zero. The export panel: PNG twice the chart, SVG, CSV of its data.
 */
import { useRef } from "react";
import { XAxis, YAxis } from "../../charts/core/Axes";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { domainOf, makeScale } from "../../charts/core/axisScale";
import { seriesColor } from "../../charts/core/palette";
import { PlotFrame, plotBox } from "../../charts/core/PlotFrame";
import css from "../../charts/core/Chart.module.css";
import own from "./Stats.module.css";

export const PLOT_W = 560;
export const PLOT_H = 280;

type Num = number | null | undefined;

const finite = (v: Num): v is number => typeof v === "number" && Number.isFinite(v);

/** The path of a line: a missing x or y ends a piece; a lone point is drawn as a dot. */
export function linePieces(xs: readonly Num[], ys: readonly Num[], sx: (v: number) => number | null, sy: (v: number) => number | null): { paths: string[]; dots: [number, number][] } {
  const paths: string[] = [];
  const dots: [number, number][] = [];
  let piece: [number, number][] = [];
  const flush = () => {
    if (piece.length === 1) dots.push(piece[0]);
    else if (piece.length > 1) paths.push(piece.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(""));
    piece = [];
  };
  for (let i = 0; i < Math.min(xs.length, ys.length); i += 1) {
    const x = xs[i];
    const y = ys[i];
    const px = finite(x) ? sx(x) : null;
    const py = finite(y) ? sy(y) : null;
    if (px !== null && py !== null) piece.push([px, py]);
    else flush();
  }
  flush();
  return { paths, dots };
}

export interface LinePlotProps {
  label: string;
  xs: readonly Num[];
  ys: readonly Num[];
  xTitle: string;
  yTitle: string;
  refY?: { value: number; label: string } | null;
  point?: { x: number; y: number; label: string } | null;
  fileName: string;
  csv: () => string | null;
}

export function LinePlot({ label, xs, ys, xTitle, yTitle, refY = null, point = null, fileName, csv }: LinePlotProps) {
  const ref = useRef<SVGSVGElement | null>(null);
  const box = plotBox(PLOT_W, PLOT_H, { left: 62, right: 16, top: 14, bottom: 46 });
  const xd = domainOf(xs);
  const yd = domainOf([...ys, refY?.value, point?.y]);
  const sx = xd ? makeScale("linear", xd, box.x0, box.x1) : null;
  const sy = yd ? makeScale("linear", yd, box.y1, box.y0) : null;
  const { paths, dots } = sx && sy ? linePieces(xs, ys, sx.map, sy.map) : { paths: [], dots: [] };
  const ry = refY && sy ? sy.map(refY.value) : null;
  const px = point && sx ? sx.map(point.x) : null;
  const py = point && sy ? sy.map(point.y) : null;
  return (
    <figure className={own.figure}>
      <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle} yTitle={yTitle}>
        {sx && sy ? (
          <>
            <XAxis scale={sx} box={box} count={6} />
            <YAxis scale={sy} box={box} count={5} />
            {ry !== null && (
              <g data-testid="plot-ref">
                <line className={css.mark} strokeDasharray="6 4" x1={box.x0} x2={box.x1} y1={ry} y2={ry} />
                <text className={css.markLabel} x={box.x1 - 4} y={ry - 4} textAnchor="end">
                  {refY!.label}
                </text>
              </g>
            )}
            {paths.map((d, i) => (
              <path key={i} d={d} fill="none" stroke={seriesColor(0)} strokeWidth={1.8} />
            ))}
            {dots.map(([x, y], i) => (
              <circle key={`d${i}`} cx={x} cy={y} r={2.5} fill={seriesColor(0)} />
            ))}
            {px !== null && py !== null && (
              <g data-testid="plot-point">
                <circle cx={px} cy={py} r={4.5} fill="none" stroke={seriesColor(1)} strokeWidth={2} />
                <text className={css.markLabel} x={px + 7} y={py - 7}>
                  {point!.label}
                </text>
              </g>
            )}
          </>
        ) : (
          <text className={css.tick} x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
            Нет данных
          </text>
        )}
      </PlotFrame>
      <ChartToolbar svgRef={ref} fileName={fileName} csv={csv} disabled={!sx || !sy} />
    </figure>
  );
}

export interface BarsMark {
  value: number | null;
  label: string;
  dashed?: boolean;
}

export interface BarsPlotProps {
  label: string;
  edges: readonly number[];
  counts: readonly number[];
  xTitle: string;
  marks: readonly BarsMark[];
  fileName: string;
  csv: () => string | null;
}

/** Bars of [edges[i]; edges[i + 1]) with their counts; marks — vertical lines with labels (observed, the interval). */
export function BarsPlot({ label, edges, counts, xTitle, marks, fileName, csv }: BarsPlotProps) {
  const ref = useRef<SVGSVGElement | null>(null);
  const box = plotBox(PLOT_W, PLOT_H, { left: 62, right: 16, top: 22, bottom: 46 });
  const ok = edges.length === counts.length + 1 && counts.length > 0;
  const xd = ok ? domainOf([...edges, ...marks.map((m) => m.value)], "linear", 0.02) : null;
  const top = Math.max(1, ...counts);
  const sx = xd ? makeScale("linear", xd, box.x0, box.x1) : null;
  const sy = makeScale("linear", { min: 0, max: top * 1.08 }, box.y1, box.y0);
  return (
    <figure className={own.figure}>
      <PlotFrame ref={ref} box={box} label={label} xTitle={xTitle} yTitle="Реализаций">
        {sx ? (
          <>
            <XAxis scale={sx} box={box} count={6} />
            <YAxis scale={sy} box={box} count={4} />
            {counts.map((c, i) => {
              const a = sx.map(edges[i]);
              const b = sx.map(edges[i + 1]);
              const y = sy.map(c);
              if (a === null || b === null || y === null || c <= 0) return null;
              return <rect key={i} x={a} y={y} width={Math.max(0.5, b - a - 0.5)} height={box.y1 - y} fill={seriesColor(0)} fillOpacity={0.85} />;
            })}
            {marks.map((m, i) => {
              const x = m.value === null ? null : sx.map(m.value);
              if (x === null) return null;
              return (
                <g key={m.label} data-testid="plot-mark">
                  <line className={css.mark} strokeDasharray={m.dashed ? "5 4" : undefined} x1={x} x2={x} y1={box.y0} y2={box.y1} />
                  <text className={css.markLabel} x={x} y={box.y0 - 6} textAnchor={i === 1 ? "end" : i === 2 ? "start" : "middle"}>
                    {m.label}
                  </text>
                </g>
              );
            })}
          </>
        ) : (
          <text className={css.tick} x={(box.x0 + box.x1) / 2} y={(box.y0 + box.y1) / 2} textAnchor="middle">
            Нет реализаций с числом
          </text>
        )}
      </PlotFrame>
      <ChartToolbar svgRef={ref} fileName={fileName} csv={csv} disabled={!sx} />
    </figure>
  );
}
