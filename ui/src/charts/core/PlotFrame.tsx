/**
 * The SVG of a chart (ADR 0019 §11): fixed viewBox (the PNG is twice its size), background, plot frame, axis
 * titles; the chart draws inside the plot box. Colours come from theme tokens; export bakes them in.
 */
import { forwardRef, type ReactNode, type PointerEvent as RPointerEvent } from "react";
import css from "./Chart.module.css";

export interface Margin {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface PlotBox {
  width: number;
  height: number;
  margin: Margin;
  /** the plot area, px of the viewBox */
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}

export const DEFAULT_MARGIN: Margin = { left: 62, right: 52, top: 18, bottom: 46 };

export function plotBox(width: number, height: number, margin: Margin = DEFAULT_MARGIN): PlotBox {
  return { width, height, margin, x0: margin.left, x1: width - margin.right, y0: margin.top, y1: height - margin.bottom };
}

export interface PlotFrameProps {
  box: PlotBox;
  label: string;
  xTitle?: string;
  yTitle?: string;
  yTitleRight?: string;
  children?: ReactNode;
  onPointerMove?: (e: RPointerEvent<SVGSVGElement>) => void;
  onPointerLeave?: () => void;
}

export const PlotFrame = forwardRef<SVGSVGElement, PlotFrameProps>(function PlotFrame({ box, label, xTitle, yTitle, yTitleRight, children, onPointerMove, onPointerLeave }, ref) {
  const { width: W, height: H, x0, x1, y0, y1 } = box;
  return (
    <svg ref={ref} viewBox={`0 0 ${W} ${H}`} width={W} height={H} className={css.svg} role="img" aria-label={label} onPointerMove={onPointerMove} onPointerLeave={onPointerLeave}>
      <rect className={css.bg} x={0} y={0} width={W} height={H} />
      {children}
      <rect className={css.frame} x={x0} y={y0} width={x1 - x0} height={y1 - y0} />
      {xTitle && (
        <text className={css.axisTitle} x={(x0 + x1) / 2} y={H - 8} textAnchor="middle">
          {xTitle}
        </text>
      )}
      {yTitle && (
        <text className={css.axisTitle} transform={`translate(14 ${(y0 + y1) / 2}) rotate(-90)`} textAnchor="middle">
          {yTitle}
        </text>
      )}
      {yTitleRight && (
        <text className={css.axisTitle} transform={`translate(${W - 10} ${(y0 + y1) / 2}) rotate(90)`} textAnchor="middle">
          {yTitleRight}
        </text>
      )}
    </svg>
  );
});
