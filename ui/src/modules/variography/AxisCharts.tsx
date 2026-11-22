/**
 * The charts of the tab «Оси» of the window «Вариография» (ТЗ 8.6.4; ADR 0022 §15): the experimental variogram of
 * each axis of the calculation with the curve of the working model along it, and the one along the holes (no model
 * curve: its direction follows the holes); the number of pairs at the points, a lag without pairs not drawn. Each
 * chart has the export panel (PNG, SVG, CSV). `overlay` — the drag handles of the window (16.24) on a chart.
 */
import { useRef, type ReactNode } from "react";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { curvesCsv } from "../../charts/variogram/scale";
import { VariogramChart, type VariogramScales, type VariogramSeries } from "../../charts/variogram/VariogramChart";
import type { CalcResult } from "./api";
import { axisSeries, chartSigma2, DOWNHOLE_LABEL } from "./calc";
import type { ModelType, VModel } from "./model";
import own from "./Variography.module.css";

/** The chart a handle is drawn on: the index of the direction, or the downhole one. */
export type ChartKey = number | "downhole";

export interface AxisChartsProps {
  calc: CalcResult;
  model: VModel | null;
  type: ModelType;
  /** the name of the files of the export («Вариография AU») */
  fileName: string;
  /** only the downhole chart (the view «Вдоль скважин») */
  downholeOnly?: boolean;
  overlay?: (chart: ChartKey, s: VariogramScales) => ReactNode;
}

function OneChart({ series, calc, model, type, fileName, overlay, wide }: { series: VariogramSeries; calc: CalcResult; model: VModel | null; type: ModelType; fileName: string; overlay?: (s: VariogramScales) => ReactNode; wide?: boolean }) {
  const ref = useRef<SVGSVGElement | null>(null);
  return (
    <figure className={own.chart} data-chart={series.label}>
      <VariogramChart ref={ref} series={[series]} type={type} model={series.u ? model : null} sigma2={chartSigma2(calc)} title={series.label} width={wide ? 760 : 420} height={wide ? 380 : 280} overlay={overlay} />
      <ChartToolbar svgRef={ref} fileName={`${fileName} ${series.label}`} csv={() => curvesCsv([series], type)} />
    </figure>
  );
}

export function AxisCharts({ calc, model, type, fileName, downholeOnly, overlay }: AxisChartsProps) {
  const { axes, downhole } = axisSeries(calc, model);
  if (downholeOnly) {
    if (!downhole) return <p className={own.empty}>Вариограмма вдоль скважин не рассчитана: включите «Вдоль скважин» в параметрах и рассчитайте снова.</p>;
    return (
      <div className={own.charts} data-testid="variography-downhole">
        <OneChart series={downhole} calc={calc} model={null} type={type} fileName={fileName} wide overlay={overlay && ((s) => overlay("downhole", s))} />
      </div>
    );
  }
  return (
    <div className={own.charts} data-testid="variography-axes">
      {axes.map((s, i) => (
        <OneChart key={`${i}:${s.label}`} series={s} calc={calc} model={model} type={type} fileName={fileName} overlay={overlay && ((sc) => overlay(i, sc))} />
      ))}
      {downhole && <OneChart key={DOWNHOLE_LABEL} series={downhole} calc={calc} model={null} type={type} fileName={fileName} overlay={overlay && ((s) => overlay("downhole", s))} />}
    </div>
  );
}
