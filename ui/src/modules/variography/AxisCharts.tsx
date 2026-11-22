/**
 * The charts of the tab «Оси» of the window «Вариография» (ТЗ 8.6.4; ADR 0022 §15): the experimental variogram of
 * each axis of the calculation with the curve of the working model along it, and the one along the holes (no model
 * curve: its direction follows the holes); the number of pairs at the points, a lag without pairs not drawn. Each
 * chart has the export panel (PNG, SVG, CSV). With `onModel` the model is edited with the mouse on the charts
 * (16.24, `chartDrag.ts`): the nugget node, the sill lines of the structures, the range lines of the axis of the
 * chart; `onDrag` tells the window a drag is on (the preview and the job wait for the release). `overlay` — more
 * handles of the window on a chart.
 */
import { useRef, useState, type PointerEvent as RPointerEvent, type ReactNode } from "react";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { toViewBox } from "../../charts/core/lasso";
import { curvesCsv } from "../../charts/variogram/scale";
import { VariogramChart, type VariogramScales, type VariogramSeries } from "../../charts/variogram/VariogramChart";
import type { CalcResult } from "./api";
import { axisSeries, chartSigma2, DOWNHOLE_LABEL } from "./calc";
import { chartHandles, dragTo, hitHandle, valueAt, type ChartKey, type DragLimits } from "./chartDrag";
import type { ModelType, VModel } from "./model";
import own from "./Variography.module.css";

export type { ChartKey } from "./chartDrag";

export interface AxisChartsProps {
  calc: CalcResult;
  model: VModel | null;
  type: ModelType;
  /** the name of the files of the export («Вариография AU») */
  fileName: string;
  /** only the downhole chart (the view «Вдоль скважин») */
  downholeOnly?: boolean;
  overlay?: (chart: ChartKey, s: VariogramScales) => ReactNode;
  /** the model dragged on the charts (absent — no handles) */
  onModel?: (m: VModel) => void;
  /** a drag starts (true) or ends (false) */
  onDrag?: (on: boolean) => void;
  limits?: DragLimits;
}

interface DragProps {
  model: VModel;
  axis: 1 | 2 | 3 | null;
  sc: VariogramScales;
  onModel: (m: VModel) => void;
  onDrag?: (on: boolean) => void;
  limits?: DragLimits;
}

/** The handles of one chart: drawn above the curves, captured on the press, the model follows the pointer. */
export function DragLayer({ model, axis, sc, onModel, onDrag, limits }: DragProps) {
  const [active, setActive] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const before = useRef<VModel | null>(null);
  const handles = chartHandles(model, axis, sc);
  const at = (e: RPointerEvent<SVGElement>) => {
    const svg = e.currentTarget.ownerSVGElement;
    return svg ? toViewBox(e.clientX, e.clientY, svg.getBoundingClientRect(), sc.box.width, sc.box.height) : null;
  };
  const down = (e: RPointerEvent<SVGElement>) => {
    const p = at(e);
    const h = p ? hitHandle(handles, p.x, p.y) : null;
    if (!h) return;
    e.stopPropagation();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    before.current = model;
    setActive(h.id);
    onDrag?.(true);
  };
  const move = (e: RPointerEvent<SVGElement>) => {
    const p = at(e);
    if (!p) return;
    if (!active) return setHover(hitHandle(handles, p.x, p.y)?.id ?? null);
    const h = handles.find((x) => x.id === active);
    if (!h) return;
    const v = valueAt(sc, p.x, p.y);
    onModel(dragTo(model, h.target, v.h, v.value, limits));
  };
  const up = (e: RPointerEvent<SVGElement>) => {
    if (!active) return;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    setActive(null);
    before.current = null;
    onDrag?.(false);
  };
  const cancel = (e: RPointerEvent<SVGElement>) => {
    if (active && before.current) onModel(before.current); // a lost capture: the model as it was
    up(e);
  };
  return (
    <g data-drag-handles={handles.length}>
      {handles.map((h) => {
        const on = h.id === active || h.id === hover;
        const point = h.target.kind === "nugget";
        return (
          <g key={h.id} data-handle={h.id} className={`${own.handle}${on ? ` ${own.handleOn}` : ""}`} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={cancel} onPointerLeave={() => !active && setHover(null)}>
            <title>{h.title}</title>
            {point ? <circle cx={h.x1} cy={h.y1} r={on ? 6 : 5} /> : <line x1={h.x1} y1={h.y1} x2={h.x2} y2={h.y2} />}
            {point ? <circle className={own.handleHit} cx={h.x1} cy={h.y1} r={9} /> : <line className={own.handleHit} x1={h.x1} y1={h.y1} x2={h.x2} y2={h.y2} />}
          </g>
        );
      })}
    </g>
  );
}

interface OneProps {
  series: VariogramSeries;
  calc: CalcResult;
  model: VModel | null;
  type: ModelType;
  fileName: string;
  overlay?: (s: VariogramScales) => ReactNode;
  wide?: boolean;
  axis: 1 | 2 | 3 | null;
  drag?: Omit<DragProps, "model" | "axis" | "sc">;
}

function OneChart({ series, calc, model, type, fileName, overlay, wide, axis, drag }: OneProps) {
  const ref = useRef<SVGSVGElement | null>(null);
  const layer = (s: VariogramScales) => (
    <>
      {overlay?.(s)}
      {drag && model && <DragLayer model={model} axis={axis} sc={s} {...drag} />}
    </>
  );
  return (
    <figure className={own.chart} data-chart={series.label}>
      <VariogramChart ref={ref} series={[series]} type={type} model={series.u ? model : null} sigma2={chartSigma2(calc)} title={series.label} width={wide ? 760 : 420} height={wide ? 380 : 280} overlay={overlay || drag ? layer : undefined} />
      <ChartToolbar svgRef={ref} fileName={`${fileName} ${series.label}`} csv={() => curvesCsv([series], type)} />
    </figure>
  );
}

export function AxisCharts({ calc, model, type, fileName, downholeOnly, overlay, onModel, onDrag, limits }: AxisChartsProps) {
  const { axes, downhole } = axisSeries(calc, model);
  const drag = onModel ? { onModel, onDrag, limits } : undefined;
  if (downholeOnly) {
    if (!downhole) return <p className={own.empty}>Вариограмма вдоль скважин не рассчитана: включите «Вдоль скважин» в параметрах и рассчитайте снова.</p>;
    return (
      <div className={own.charts} data-testid="variography-downhole">
        <OneChart series={downhole} calc={calc} model={model} type={type} fileName={fileName} wide axis={null} drag={drag} overlay={overlay && ((s) => overlay("downhole", s))} />
      </div>
    );
  }
  return (
    <div className={own.charts} data-testid="variography-axes">
      {axes.map((s, i) => (
        <OneChart key={`${i}:${s.label}`} series={s} calc={calc} model={model} type={type} fileName={fileName} axis={calc.directions[i]?.axis ?? null} drag={drag} overlay={overlay && ((sc) => overlay(i, sc))} />
      ))}
      {downhole && <OneChart key={DOWNHOLE_LABEL} series={downhole} calc={calc} model={model} type={type} fileName={fileName} axis={null} drag={drag} overlay={overlay && ((s) => overlay("downhole", s))} />}
    </div>
  );
}
