/**
 * The tree drawn horizontally — distance to the right, the leaves down the left — with the correlation matrix
 * beside it in the order of the leaves (ТЗ 7.27; перенос `DendrogramChart` BF). A cut colours the groups and
 * is drawn as a dashed line. SVG of its own size (by the number of fields): the PNG is twice it.
 */
import { forwardRef } from "react";
import type { DendrogramResult } from "../../modules/charts/api";
import { formatNumber } from "../core/axisScale";
import css from "../core/Chart.module.css";
import { clustersAt, corrColor, groupColor } from "./model";

const ROW = 26;
const LEFT = 120;
const TREE = 300;
const GAP = 24;
const TOP = 70;

/** The size of the chart for n fields (with or without the matrix). */
export function dendrogramSize(n: number, matrix: boolean): { width: number; height: number; cell: number; row: number } {
  const cell = Math.max(18, Math.min(34, 480 / Math.max(1, n)));
  const row = Math.max(ROW, matrix ? cell : ROW);
  return { width: LEFT + TREE + GAP + (matrix ? n * cell : 0) + 20, height: TOP + n * row + 46, cell, row };
}

export interface DendrogramChartProps {
  result: DendrogramResult;
  cut: number | null;
  matrix: boolean;
  values: boolean;
  distanceLabel: string;
}

export const DendrogramChart = forwardRef<SVGSVGElement, DendrogramChartProps>(function DendrogramChart({ result, cut, matrix, values, distanceLabel }, ref) {
  const n = result.fields.length;
  const order = result.order;
  const { width: W, height: H, cell, row } = dendrogramSize(n, matrix);
  const maxD = Math.max(result.max_distance, cut ?? 0, 1e-9) * 1.05;
  // scipy's layout: leaves at 5, 15, 25…; distance 0 at the leaves
  const y = (i: number) => TOP + ((i - 5) / 10) * row + row / 2;
  const x = (d: number) => LEFT + TREE - (d / maxD) * TREE;
  const groups = cut !== null && n ? clustersAt(result.linkage, n, order, cut) : null;
  /** A link below the cut takes the colour of its group: that of the leaf under its left end. */
  const linkColor = (ic: number[], dc: number[]): string | undefined => {
    if (!groups || cut === null || dc[1] > cut) return undefined;
    const pos = Math.min(n - 1, Math.max(0, Math.floor((ic[0] - 5) / 10 + 1e-9)));
    return groupColor(groups.leaf[order[pos]]) ?? undefined;
  };
  const ticks = Array.from({ length: 5 }, (_, k) => (maxD / 4) * k);
  const mx0 = LEFT + TREE + GAP;
  const bottom = TOP + n * row;
  return (
    <svg ref={ref} viewBox={`0 0 ${W} ${H}`} width={W} height={H} className={css.svg} role="img" aria-label="Дендрограмма">
      <rect className={css.bg} x={0} y={0} width={W} height={H} />
      {ticks.map((t) => (
        <g key={t}>
          <line className={css.grid} x1={x(t)} x2={x(t)} y1={TOP - 6} y2={bottom} />
          <text className={css.tick} x={x(t)} y={bottom + 15} textAnchor="middle">
            {formatNumber(t)}
          </text>
        </g>
      ))}
      <text className={css.axisTitle} x={LEFT + TREE / 2} y={H - 8} textAnchor="middle">
        {distanceLabel}
      </text>
      <g data-links={result.icoord.length}>
        {result.icoord.map((ic, k) => {
          const dc = result.dcoord[k];
          const c = linkColor(ic, dc);
          return <polyline key={k} className={css.link} points={ic.map((v, j) => `${x(dc[j]).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} style={c ? { stroke: c } : undefined} />;
        })}
      </g>
      {cut !== null && cut <= maxD && (
        <g data-cut={cut}>
          <line className={css.mark} x1={x(cut)} x2={x(cut)} y1={TOP - 10} y2={bottom} strokeDasharray="5 4" />
          <text className={css.markLabel} x={x(cut) + 4} y={TOP - 14}>
            порог {formatNumber(cut)}
          </text>
        </g>
      )}
      {order.map((f, i) => {
        const c = groups ? groupColor(groups.leaf[f]) : null;
        return (
          <text key={f} className={css.leaf} x={LEFT - 8} y={y(5 + i * 10)} textAnchor="end" dominantBaseline="middle" style={c ? { fill: c } : undefined} data-leaf={result.fields[f]}>
            {result.fields[f]}
          </text>
        );
      })}
      {matrix && (
        <g data-matrix={n}>
          {order.map((fi, i) =>
            order.map((fj, j) => {
              const r = result.correlation[fi]?.[fj] ?? null;
              return (
                <g key={`${i}-${j}`}>
                  <rect x={mx0 + j * cell} y={TOP + i * row + (row - cell) / 2} width={cell - 1} height={cell - 1} fill={corrColor(r)}>
                    <title>{`${result.fields[fi]} — ${result.fields[fj]}: r = ${formatNumber(r, 4)}, общих проб ${(result.pairs[fi]?.[fj] ?? 0).toLocaleString("ru-RU")}`}</title>
                  </rect>
                  {values && cell >= 24 && (
                    <text className={css.cellValue} x={mx0 + j * cell + cell / 2} y={TOP + i * row + row / 2} textAnchor="middle" dominantBaseline="middle">
                      {r === null ? "—" : r.toFixed(2).replace("0.", ".")}
                    </text>
                  )}
                </g>
              );
            }),
          )}
          {order.map((f, j) => (
            <text key={f} className={css.tick} transform={`translate(${mx0 + j * cell + cell / 2} ${TOP - 6}) rotate(-45)`} textAnchor="start">
              {result.fields[f]}
            </text>
          ))}
        </g>
      )}
    </svg>
  );
});
